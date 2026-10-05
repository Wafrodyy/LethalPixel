EXPORT BLOCKERS ET DECORATIFS V73

Extraire ce ZIP dans un dossier, puis ouvrir PowerShell dans ce dossier.
Installer les dependances :
  py -m pip install -r .\requirements.txt

Tester une salle :
  py .\export_objects.py --assets "D:\V73 ripper\Export\AssetRipper_export_20261002_143751\ExportedProject\Assets" --room "4x4BigStairTile" --out ".\Objects_PNG"

Tout exporter :
  py .\export_objects.py --assets "D:\V73 ripper\Export\AssetRipper_export_20261002_143751\ExportedProject\Assets" --out ".\Objects_PNG"

Pour utiliser les noms choisis : ajouter --translator "chemin\Translator.json"
Le JSON peut etre celui par salle ou celui regroupe par interieur.

SORTIES
Objects_PNG/2px_par_unite/{Facility,Mansion,Mineshaft}/{Blocker,Decoratif}/*.png
Objects_PNG/20px_par_unite/{Facility,Mansion,Mineshaft}/{Blocker,Decoratif}/*.png
Objects_PNG/report.json : sources, salles, dimensions, erreurs et textures manquantes.

Vue orthographique du dessus, X vers la droite, Z vers le haut, fond transparent.
Les deux resolutions sont calculees depuis les meshes, sans agrandir un PNG.
Une unite Unity correspond exactement a 2 ou 20 pixels, plus 1 pixel de marge.
Decoratif contient uniquement les Decorator variables. Permanent est exclu.
NavMesh et MapRadar sont exclus par
le classement du catalogue. La racine d'un objet variable est activee pour
l'export ; ses enfants desactives restent desactives.
Les sous-objets d'un groupe sont reunis dans le meme PNG.
Le suffixe de geometrie conserve les variantes de meme nom et evite les
collisions. report.json conserve la provenance par salle.

LIMITES
Rendu des textures de couleur et des couleurs de materiaux, sans reproduire
les shaders Unity, normal maps, lumieres ou animations. Les references de
mesh manquantes et textures absentes sont signalees. Les PrefabInstance
imbriques et meshes externes streams ne sont pas pris en charge.
Les SkinnedMeshRenderer ne sont pas pris en charge dans cette version.
Les surfaces verticales sont representees par une ligne de 1 pixel minimum
a chaque resolution, coloree par le materiau (texture echantillonnee au centre).
Les objets tres fins peuvent encore produire une projection vide : le rapport le signale. Les rendus sans texture disponible
utilisent la palette de l'interieur et sont marques partial.
Utiliser le dossier Assets COMPLET, pas le ZIP de sources Translator seul.

MISE A JOUR : rotations et echelles des parents conservees lors de l'isolation.
Sous-groupes NavMesh, MapRadar, Collider(s), Collision et KillTrigger exclus.
Progression affichee apres chaque objet traite, sans affichage periodique.
Un objet complexe peut prendre du temps avant la prochaine ligne.
Pour un resultat propre, utiliser --out ".\Objects_PNG_corriges" : les anciens
PNG ne sont pas supprimes automatiquement.

TAILLES EN UNITES DU JEU
Chaque lancement produit aussi Translator_by_interior_sizes.json dans --out.
Format Facility/Mansion/Mineshaft -> Room/Decoratif/Blocker -> liste de mappings
{ "NomInGame": "NomChoisi", "Size": {"x": ..., "y": ..., "z": ...}, "Variant": "..." }.
X et Z donnent l'encombrement de dessus, Y la hauteur. Mesures de la boite
englobante de la geometrie rendue, dans son orientation de salle/prefab,
avec echelles heritees. Pas de marge PNG ni d'epaisseur fictive de 1 pixel.
Les memes noms avec une geometrie differente restent en entrees separees.
Size null signifie une geometrie manquante/non prise en charge, pas une taille nulle.
Les decoratifs permanents sont exclus comme pour les PNG.
Pour mesurer sans refaire les images : ajouter --sizes-only a la commande.

Room contient les dimensions des meshes fixes actifs de chaque salle, hors
branches variables/blockers et groupes techniques. La hauteur inclut le plafond
si son mesh est present. Les decors fixes font partie de la salle.

EXPORT DES SALLES AVEC DECORS PERMANENTS
Ajouter --rooms-only et utiliser --out ".\Rooms_PNG". Ne pas mettre --sizes-only
pour obtenir les images. Sorties 2px_par_unite/INTERIOR/Room et
20px_par_unite/INTERIOR/Room. Les selections variables et blockers sont retires
avec leurs descendants. Les meshes de plafond/toit sont exclus des PNG par
leur nom ou materiau ; les dimensions du JSON gardent la salle complete.
Une ligne par salle terminee. --room permet de tester une seule salle.
Les meshes fixes presents dans le prefab sont reunis dans un PNG. Les objets
crees uniquement par des scripts au runtime ne sont pas reconstitues.

VUES DES SALLES
Les salles sont orientees avec la verticale deduite du local-up des Doorway.
Le JSON explique original_up et up_source ; Y dans Size est desormais la
hauteur apres cette correction (CloverTile a une verticale Z dans son prefab).
Sol de reference = porte la plus basse, sinon point le plus bas du mesh.
__sol_5u.png : coupe horizontale a sol + 5 unites, avec interpolation des UV.
__haut_complet.png : seconde vue si le sommet visible atteint sol + 9 unites
ou davantage. Les deux vues gardent le meme cadrage pour etre superposees.
Plafonds/toits identifies toujours exclus. Le rapport contient le sol et
l'orientation utilises ; une salle sans portes utilise Y par defaut.

FILTRE TECHNIQUE RENFORCE
Exclusion des groupes Nav, NavMesh, RadarBox et noms contenant Collider ou
Trigger. Le materiau technique V73 des volumes de trigger est exclu meme
sur un Cube situe hors d'un groupe technique. Aucun filtre par couleur.
Les exclusions sont inscrites dans report.json.

CORRECTION DES BLOCS GRIS ET DIAGONALES
Les cubes Unity primitifs ayant un collider et le materiau par defaut sont
exclus, meme hors des groupes nommes Colliders/Nav. Les meshes visuels gris
ordinaires restent conserves. Les PNG de salles n'ajoutent plus de lignes
artificielles de 1 pixel aux triangles verticaux ; cette representation
reste active pour les objets/blockers exportes individuellement.

SALLES DE PLUS DE 15 UNITES AU-DESSUS DU SOL
Export de trois vues : __sol_5u, __sol_12u et __haut_complet, a 2 et 20
pixels par unite. La vue a 12u est une coupe a sol + 12, gardant le cadrage
des deux autres vues. Les salles de 9 a 15 unites conservent deux vues.

CHOISIR UN INTERIEUR
--interior Facility / Mansion / Mineshaft : analyse uniquement le flow
correspondant. --assets peut pointer vers un autre export AssetRipper,
notamment V81, sans changer les chemins internes du programme.
Les nouveaux assets et shaders V81 doivent etre verifies via report.json.

EXPORT COMPLET V81 DEPUIS D:\LethalPixel-main\Coding
1. py .\generate_translator.py --assets "D:\V81_Export\ExportedProject\Assets" --out ".\Translator_V81.json"
   Produit aussi Translator_V81_by_interior.json et Translator_V81_report.json.
2. py .\export_objects.py --assets "D:\V81_Export\ExportedProject\Assets" --translator ".\Translator_V81.json" --include-rooms --out ".\Export_V81"
   Produit les salles + decors fixes, les decors variables et les blockers.
   Tous les interieurs, 2 et 20 pixels/unite, tailles et rapports.
--include-rooms ajoute les PNG de salles a l'export normal des objets.
Le Translator V81 conserve les noms choisis dans ce fichier lors des relances.
