# Durée d'un passage : 3 min 13 s → quelques secondes

## Mesure (2026-11-05, données réelles)

1 434 entrées d'index, **662 Mo**, médiane **540 Ko** par entrée. Trois clés font 98 % du volume :

| clé | poids moyen | part |
|---|---|---|
| `remoteMcpServersConfig` | 454 Ko/fichier | 82,8 % |
| `enabledMcpTools` | 82 Ko/fichier | 15,0 % |
| `promptAppendSnapshot` | 10 Ko/fichier | 1,8 % |

Aucune des trois n'est lue par le script (grep littéral : zéro occurrence).

Répartition du temps, extrapolée de 400 fichiers aux 1 434 :

| étape | coût |
|---|---|
| `os.stat` | 0,01 s |
| lecture des octets | 2,9 s |
| `sha256` | 1,6 s |
| **`json.loads`** | **51,6 s** |

Soit ~52 s de parsing sur ~56 s de travail en isolation : **94 %**. Le passage observé fait 3 min 13 s (et jusqu'à 23 min sous charge) : même travail, sous la priorité de launchd et en concurrence avec Desktop.

## Contrainte découverte avant d'écrire (elle a changé la conception)

Ligne 251, quand `merge()` produit un objet qui n'est égal à aucun des deux côtés, les octets écrits sont `json.dumps(merged)`. Et `merge()` fait `set(x) | set(y)` puis `dict(x)` : il lui faut l'objet **entier**.

→ **Un cache de projection ne doit jamais alimenter une écriture.** Sinon chaque entrée fusionnée perdrait ses trois clés lourdes, soit 98 % de son contenu. Le cache ne sert qu'à *écarter* du travail.

## Conception : deux phases

**Phase 1 — écarter, sur cache.** `os.stat` sur les 1 434 fichiers. Cache `{chemin: {mtime_ns, size, sha, proj}}`, où `proj` ne contient que les champs servant aux décisions d'écartement (`cliSessionId`, `scheduledTaskId`). À l'identique du stat → on prend `sha` et `proj` sans lire ni parser. Sinon → lecture, parsing, validation de schéma, remplissage du cache.

Candidats retenus :
- présent des deux côtés et `sha` différent (un `sha` égal, c'est exactement le `a[0] == b[0]` d'aujourd'hui) ;
- présent d'un seul côté, et `proj` ne le fait pas écarter (transcript absent → `dead_skipped` ; `scheduledTaskId` → `scheduled_task_skipped`).

**Phase 2 — décider, sans cache.** Pour les seuls candidats : lecture des octets et parsing complet, puis la logique actuelle **inchangée**.

Aujourd'hui : 415 communes dont 384 identiques, et 601 à un seul côté dont 596 écartées. Soit ~36 fichiers à parser au lieu de 1 434.

**Équivalence sémantique.** Le seul cas où bytes et objets divergent (`a[0] != b[0]` mais `a[1] == b[1]`) tombe dans les candidats, donc est parsé en phase 2 : même verdict. La validation de schéma reste exercée sur chaque *version* de chaque fichier, puisqu'un fichier modifié rate le cache.

## Étapes

1. Ajouter `took=<s>` à la ligne `result=` du journal → **verify** : une ligne de `sync.log` porte la durée.
2. `load_org` : `stat` → lecture → `stat` de contrôle, et `seen` prend le stat final → **verify** : test où le fichier bouge entre les deux stats.
3. Cache (fichier atomique dans `BK`, sous le verrou, estampillé par un numéro de format + le hash de la liste des clés lourdes). Illisible, corrompu ou périmé → parsing à froid, **jamais** d'`Abort`, et un échec d'écriture du cache ne fait pas échouer le passage → **verify** : test avec cache corrompu, le passage réussit.
4. Phase 1 / phase 2 dans `plan()` → **verify** : test d'équivalence, un passage à froid et un passage à chaud produisent les mêmes ops.
5. Fixtures de test enrichies des trois clés lourdes → **verify** : un test échoue si le cache alimente une écriture (octets de destination identiques à la source, clés lourdes comprises).
6. Relecture indépendante, puis mesure en production → **verify** : `took=` avant / après, premier passage à froid exclu.

## Garde-fous

- Le cache ne produit jamais d'octets à écrire.
- Un cache absent ou douteux dégrade la vitesse, jamais la correction.
- Chaque nouveau test doit échouer quand on retire la ligne qu'il couvre.

## Revue

(à remplir en fin de chantier)
