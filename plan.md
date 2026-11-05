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

### Ce que j'avais mal attribué

Le plan disait « 94 % du temps dans `json.loads` ». C'était vrai en isolation et faux comme
explication. **Le coût dominant était la mémoire.** L'ancien `load_org` gardait les octets *et*
l'objet parsé des 1 436 entrées en même temps. Mesuré à froid sur les vraies données, à vide :

| | durée | pic RSS |
|---|---|---|
| installé | **1 927 s** | **1 206 Mo** |
| nouveau, à froid | 150 s | 69 Mo |
| nouveau, à chaud | 15 à 34 s | 72 Mo |

1,2 Go de pic, donc du swap — et c'est ce qui explique qu'un même code ait mis 59 s, 3 min 13 s
puis 23 min selon la pression mémoire du moment. La mesure en isolation ne pouvait pas le voir :
elle lisait 400 fichiers, pas 1 436, donc elle ne touchait jamais la limite. **Leçon : mesurer
l'empreinte mémoire d'un passage complet, pas seulement le temps CPU d'un échantillon.**

### Fait

1. `took=` et `cache=` dans le journal, et un passage sans op y est désormais tracé. ✔
2. `read_paired()` apparie stat et octets. ✔ Un défaut de ma première version — renvoyer le stat
   d'*après* la lecture dans le cas dégénéré — a été trouvé en relecture et reproduit par une sonde
   en process : il faisait croire à `moved_since` que rien n'avait bougé.
3. Cache tolérant aux pannes : illisible, corrompu, d'un autre format ou périmé → passage à froid,
   jamais d'`Abort`. ✔ Rejet **par entrée**, pas du cache entier.
4. Deux phases dans `plan()`. ✔ Équivalence prouvée sur les vraies données : 5 ops identiques entre
   l'installé, le neuf à froid et le neuf à chaud, les 2 conflits connus vus de part et d'autre.
5. Fixtures portant les trois clés lourdes. ✔
6. Relecture indépendante, puis mesure. ✔

### Ce que la relecture a changé

L'invariant tient : aucun chemin où des octets ou un objet venus du cache atteignent une écriture,
vérifié sur les dix formes d'op dans un passage 100 % chaud. Mais six constats, tous reproduits :

- une valeur non hachable en cache tuait le passage **toutes les 5 minutes indéfiniment**, parce que
  le `TypeError` précédait `save_cache` : le mauvais cache n'était jamais réécrit ;
- une projection qui *ment* est bien formée, donc indétectable, et cachait une session pour
  toujours. Non détectable → **borné** : le cache périme en un jour (`CCD_CACHE_TTL`) ;
- une entrée supprimée en cours de passage tuait tout le passage, contre la promesse de l'en-tête ;
- les règles d'exclusion n'étaient jamais rejouées sur les octets réellement lus ;
- `parse_entry` ne typait pas les deux champs qui pilotent une exclusion (préexistant) ;
- `--dry-run` écrit le cache en affichant « NO FILES MODIFIED », et le test censé le vérifier ne
  regardait pas le bon dossier.

Mon test des clés lourdes n'exerçait pas l'invariant : il remplissait le cache depuis une paire
identique, ce qui laissait le côté réécrit en *cache miss*. Refait selon la recette du plan.

### Assumé, non corrigé

Une entrée du cache n'est distinguée que par `(mtime_ns, size)`. Le relecteur a montré qu'une
écriture d'`apply()` de taille rigoureusement identique n'était séparée que par 466 µs de `mtime`.
C'est l'hypothèse sur laquelle tout repose. Le correctif de `read_paired` la rend sûre — un stat
strictement plus vieux ne peut causer qu'un *miss* — et la péremption borne le reste à un jour.

Hors périmètre, relevé en passant : quand *toutes* les ops d'un passage ont bougé, le script sort en
code 1, donc launchd enregistre un échec là où il n'y a que « rien à faire ». Cosmétique.

### Reste

61 tests. Chacun des sept nouveaux est prouvé en retirant la seule ligne qu'il couvre. Installé le
05/11 ; rien n'est poussé.
