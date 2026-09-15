# Fiche d'auto-évaluation

POC d'un agent IA de triage médical — Centre Hospitalier Saint-Aurélien.
Benoit Girard, IA Engineer.

Cette fiche met les compétences évaluées et les livrables attendus en regard des
preuves produites, et signale ce qui n'est pas acquis. Elle sert de base de
discussion avec le mentor avant la soutenance.

Les quelques chiffres cités ici le sont pour rendre un raisonnement lisible ;
aucun n'y fait autorité. Ils vivent dans `reports/evaluation_results.json`,
`reports/training/` et `data/processed/metadata.json`, d'où le rapport technique
les reprend par substitution. Toute valeur citée en soutenance se retrouve dans
ces fichiers.

---

## Compétence 1 — Ajuster les paramètres des procédures d'entraînement afin d'optimiser la performance

| Élément | Preuve |
|---|---|
| Fine-tuning supervisé avec LoRA | `src/chsa_triage/training/sft.py`, `scripts/03_train_sft.py`, notebook 02 |
| Alignement par préférences (DPO) | `src/chsa_triage/training/dpo.py`, `scripts/05_train_dpo.py`, notebook 03 |
| **Comparaison de configurations** | `scripts/02_tune_hyperparameters.py` → `reports/training/comparaison_hyperparametres.json` |
| Hyperparamètres centralisés et versionnés | `src/chsa_triage/config.py`, et les valeurs **réellement utilisées** dans `reports/training/*.json` |
| Suivi d'expériences | MLflow local (`mlruns/`), résumés JSON versionnés dans `reports/training/` |
| Validation intermédiaire et sélection du meilleur point | évaluation à chaque époque, `load_best_model_at_end` |
| Jeu de validation côté alignement | découpage dédié dans `train_dpo`, sans quoi seules les courbes d'entraînement seraient disponibles |

**Ce que je mettrais en avant.** Quatre configurations ont été comparées dans des
conditions identiques, et jugées sur deux critères : la perte de validation *et*
l'exactitude de triage mesurée par génération. Les deux ne concordent pas
toujours, et c'est la seconde qui compte pour le service.

**Le point technique qui a le plus compté** n'est pas un hyperparamètre, et c'est
celui sur lequel j'aimerais être challengé.

Le réglage des hyperparamètres a rendu **0,000 d'arrêts nets sur les quatre
configurations**, à l'unité près, pendant que l'exactitude tournait autour de
0,85. Un zéro exact répété quatre fois n'est pas un modèle sous-entraîné. Les
fichiers livrés ont été régénérés après correction : trois configurations sur
quatre portent 1,000 d'arrêts nets, `r16_lr1e-4` 0,983. La pièce qui établit le
diagnostic est `reports/training/comparaison_modules.json`, où la configuration
d'origine est rejouée à côté de la corrigée. J'ai écarté la mesure (vérifiée sur
un modèle jouet) et le budget de génération (98 jetons médians pour un plafond
de 220), puis inspecté les poids du modèle de base.

Dans `Qwen3-1.7B-Base`, les vingt-cinq jetons ChatML sont un **seul et même
vecteur jamais entraîné** — écart maximal entre eux : exactement 0,0 ;
`<|im_start|>` et `<|im_end|>` ont une similarité cosinus de 1,000 — et la tête de
sortie est liée à la matrice d'embeddings, que LoRA gèle. Le modèle ne pouvait
donc **structurellement pas** émettre le jeton de fin. Déclarer ce jeton réparait
la tuyauterie, pas la capacité à le produire.

La correction — entraîner la tête de sortie avec les projections — fait passer les
arrêts nets de 0 % à 100 %, l'exactitude de 0,850 à 0,900, et la réponse de 220 à
97 jetons, soit **la latence de service divisée par deux**. Entraîner les seuls
embeddings ne sert à rien, ce qui est contre-intuitif : la bibliothèque
d'adaptation en recopie le module et rompt le lien avec la tête.

À noter, parce que la question viendra : le correctif d'Unsloth prévu pour ce cas,
`fix_untrained_tokens`, ne détecte que les lignes d'embedding **exactement
nulles**. Ici elles valent 0,375. Il serait resté sans effet.

---

## Compétence 2 — Évaluer les performances de l'infrastructure sous-jacente au modèle d'apprentissage

| Élément | Preuve |
|---|---|
| Métriques cliniques avec intervalles de confiance | `src/chsa_triage/evaluation/metrics.py` (Wilson) |
| **Sous-triage** comme métrique de sécurité principale | `undertriage_rate`, publiée avec son intervalle |
| Coût de la prudence (surclassement) | `overtriage_rate` |
| **Contrôles de sécurité** : recommandation incohérente, diagnostic affirmé, hors-langue, constante inventée ou falsifiée, structure incomplète, niveau hors taxonomie | `src/chsa_triage/evaluation/safety.py` |
| **Quatre références** : classe majoritaire, prudence maximale, règle explicite, classifieur classique | `src/chsa_triage/evaluation/baselines.py` |
| Jeu d'évaluation indépendant, écrit à la main | `src/chsa_triage/data/clinical_eval_set.py` |
| Séparation des jeux vérifiée par le code | `check_no_leakage`, le script de préparation échoue en cas de fuite |
| Latence et débit, mesurés de bout en bout sur `POST /triage` — génération, règle, anonymisation et journal compris | `src/chsa_triage/evaluation/latency.py`, `scripts/07_benchmark_endpoint.py` |
| Empreinte d'entraînement (durée, mémoire GPU) | mesurée et journalisée dans `reports/training/*.json` |

**Ce que je mettrais en avant.** L'évaluation initiale était circulaire : les
étiquettes étaient produites par une règle à mots-clés, et le modèle était évalué
sur ces mêmes étiquettes. Réappliquer la règle au jeu d'évaluation retrouvait
98,7 % des étiquettes, soit mieux que le modèle fine-tuné. Le protocole ne
mesurait donc rien. Le jeu d'évaluation actuel est écrit à la main, ses étiquettes
viennent d'un raisonnement clinique, et la règle y est évaluée **à côté** du
modèle comme référence à battre.

**Le détail auquel je tiens.** Un contrôle de sécurité doit lui-même être
contrôlé. Les 70 réponses de référence du catalogue sont conformes par
construction : les passer au contrôle ne doit rien signaler. Ce test a d'abord
échoué sur dix d'entre elles, parce que la lecture d'une réponse retire les
accents alors que les motifs de sécurité en portaient. Le rapport aurait publié
un taux d'incohérence fabriqué, contre le modèle. Un contrôle trop strict rend la
mesure aussi fausse qu'un contrôle trop laxiste.

**Ce que je ne revendique pas.** Le jeu d'évaluation compte quelques dizaines de
cas : les intervalles de confiance sont larges et ne permettent de trancher que
des écarts francs. C'est dit dans le rapport, et c'est la raison pour laquelle la
première étape de la feuille de route est une annotation clinique indépendante.

---

## Compétence 3 — Automatiser le déploiement en intégrant les évolutions du modèle

| Élément | Preuve |
|---|---|
| API de service et contrat OpenAPI | `src/chsa_triage/serving/`, `/openapi.json` |
| Conteneurisation et pile vLLM | `deploy/Dockerfile.api`, `deploy/docker-compose.yml` |
| Intégration continue | `.github/workflows/ci.yml` : style, bandit, pip-audit, tests avec seuil de couverture, **construction de l'image puis démarrage et appel du service** |
| Déploiement continu | `.github/workflows/cd.yml` : image à chaque poussée, **version de modèle étiquetée sur le Hub**, déploiement du couple (image, révision) puis **vérification de la santé de l'endpoint** |
| Traçabilité des interactions | `src/chsa_triage/serving/audit.py`, journal anonymisé, modèle réellement chargé, durée de conservation |
| Sécurité de l'endpoint | clé obligatoire au démarrage, comparaison à temps constant, quota par appelant — identité de comptage non falsifiable —, moteur d'inférence qui réclame lui aussi la clé, sonde de santé qui ne révèle pas son adresse, conteneur sans privilège |
| Surveillance après déploiement | seuils et réactions dans `deploy/README.md` et le rapport |

**Ce que je mettrais en avant.** Le pipeline distingue deux objets qui évoluent à
des rythmes différents : la passerelle, reconstruite à chaque poussée, et le
modèle, dont une étiquette `modele-v*` fige une révision citable sur le Hub. Le
déploiement relance l'hébergeur — Modal — sur cette révision, et le serveur
d'inférence recharge les poids depuis le Hub à cette révision précise. C'est ce qui permet de
livrer une nouvelle version du modèle sans reconstruire quoi que ce soit d'autre
— l'inverse du schéma où l'image embarque les poids.

**Le détail auquel je tiens.** L'intégration continue ne se contente pas de
construire l'image : elle la démarre, appelle `/health`, et vérifie qu'un appel
sans clé est bien refusé. Une image qui ne démarre pas n'est pas une image, et
une authentification non testée n'en est pas une.

---

## Outils et corpus du cahier des charges

Chaque élément nommé ou lié par l'énoncé, et l'endroit où il sert.

| Élément du cahier des charges | Où il est utilisé |
|---|---|
| **Qwen3-1.7B-Base** | `config.py` — modèle de base, jamais la variante Instruct |
| **Unsloth** | `src/chsa_triage/training/` — noyaux de fine-tuning LoRA et d'alignement |
| **PyTorch** | pile d'entraînement, groupe de dépendances `train` |
| **Hugging Face Transformers** | chargement du modèle et du tokenizer, génération |
| **PEFT (LoRA)** | configuration des adaptateurs, fusion vers le modèle servi |
| **TRL** | `SFTTrainer` et `DPOTrainer` |
| **MLflow** | `training/tracking.py`, base locale + résumés JSON versionnés |
| **Presidio** | `data/anonymize.py`, et anonymisation du journal d'audit à l'exécution |
| **vLLM** | `deploy/docker-compose.yml`, moteur d'inférence de l'endpoint |
| **FastAPI** | `serving/api.py`, passerelle et contrat OpenAPI |
| **Docker** | `deploy/Dockerfile.api` et la pile complète en conteneurs |
| **GitHub Actions** | `.github/workflows/ci.yml` et `cd.yml` |
| **MediQAl** (`ANR-MALADES/MediQAl`) | `corpus_sources.load_mediqal` — socle de vignettes cliniques françaises |
| **FrenchMedMCQA** (`nthngdy/frenchmedmcqa`) | `corpus_sources.load_frenchmedmcqa` |
| **MedQuAD** (`keivalya/MedQuad-MedicalQnADataset`) | `corpus_sources.load_medquad` |
| **UltraMedical-Preference** (`TsinghuaC3I/UltraMedical-Preference`) | jeu DPO de contrôle, `evaluation/preference.py` |
| Étude JAMA sur le raisonnement diagnostique assisté | citée dans le rapport, à l'appui du cadrage « aide à la décision, pas décision » |

Un seul ajout hors cahier des charges : **MedMCQA**, faute de vignettes cliniques
anglophones dans les corpus imposés. Il est signalé comme tel dans la carte de
données et dans le rapport.

---

## Livrables

| # | Livrable | État | Preuve |
|---|---|---|---|
| 1 | Dataset médical bilingue, anonymisé, versionné | livré | jeu d'évaluation et fiche d'audit versionnés, jeux d'entraînement publiés sur le Hub et reconstruits par `scripts/01`, carte dans `data/README.md` |
| 2 | Modèle spécialisé (SFT + LoRA, puis DPO) | livré | poids sur le Hub, hyperparamètres et courbes dans `reports/training/` |
| 3 | Endpoint de démonstration servi par vLLM | voir note | pile complète et mesurée ; adresse publique dans la fiche du livrable |
| 4 | Pipeline CI/CD GitHub Actions | livré | `.github/workflows/`, exécutions consultables dans l'onglet Actions |
| 5 | Rapport technique (20 pages maximum) | livré | `reports/rapport_technique.pdf`, généré depuis `rapport_technique.template.md` |
| 6 | Support de soutenance | livré | `reports/soutenance_chsa.pptx`, généré depuis `scripts/10_build_slides.py` |

Le rapport et la présentation sont **produits par script** à partir des fichiers
de résultats : aucun chiffre n'y est recopié à la main, donc aucun ne peut se
désynchroniser du code.

**Note sur le livrable 3.** C'est le seul dont l'état n'est pas « livré », et il
mérite d'être précisé plutôt que résumé d'un mot. Ce qui est démontrable sans
rien réserver : la pile complète démarre en une commande
(`docker compose -f deploy/docker-compose.yml up`), l'image de la passerelle est
construite et démarrée par l'intégration continue à chaque exécution, et les
latences publiées au rapport sont mesurées de bout en bout sur `POST /triage`.
Ce qui dépend d'un tiers : l'adresse publique, qui suppose un hébergeur avec GPU
à la demande. Le déploiement Modal est décrit dans `deploy/README.md` et se
relance en une commande ; l'adresse en vigueur est inscrite dans la fiche du
livrable 3 de l'archive, qui est régénérée avec elle. Un endpoint de
démonstration n'est pas un service hospitalier : il n'a ni disponibilité
garantie, ni HTTPS géré par le CHSA, ni supervision — trois des points restés
ouverts dans la grille de passage en production.

---

## Limites assumées, et points de discussion

**Le catalogue clinique n'a pas été validé par un urgentiste.** C'est la limite
principale du projet. Elle est inscrite dans le rapport, dans la carte de données
et dans la fiche du modèle, et elle conditionne les critères go/no-go.

**Les vignettes d'entraînement sont synthétiques.** Elles sont variées et
cliniquement cohérentes, mais elles n'ont pas le désordre du langage réel. Le jeu
d'évaluation écrit à la main atténue ce biais sans le supprimer.

**MediQAl porte le volume authentique francophone** : 3 075 vignettes
cliniques distinctes, dont 569 avec constantes lisibles. C'est le seul des quatre
corpus imposés à décrire des patients plutôt que des questions d'examen.

**FrenchMedMCQA n'a pas pu porter la partie francophone** : 1 080 questions, dont
six seulement sont reconnues comme présentation de patient — et aucune ne porte
de signe de triage identifiable. MediQAl et le générateur de vignettes s'en chargent,
ce qui est dit explicitement dans la carte de données.

**MedMCQA a été ajouté hors cahier des charges**, faute de vignettes cliniques
anglophones dans les corpus imposés. L'ajout est documenté, pas dissimulé.

**L'alignement DPO ne s'appuie pas sur UltraMedical-Preference**, contrairement à
ce que prévoit le cahier des charges. Ses réponses sont de longues dissertations
en anglais et 38,6 % de ses paires sont étiquetées par la seule longueur : les
utiliser apprendrait au modèle à violer le contrat de sortie. Le corpus sert à
l'évaluation de l'alignement. L'écart est déclaré dans le rapport et dans la
carte de données.

**Aucune vérité terrain externe n'entre dans les chiffres publiés.** Le
catalogue, la règle de triage et le jeu d'évaluation ont la même source, et les
deux premiers ont le même auteur que le troisième. Une évaluation sur des cas
rédigés par un tiers reste à construire ; c'est la limite qu'il faut lever en
premier après la relecture clinique.

### Questions que j'aimerais poser

1. Le jeu d'évaluation devrait-il être annoté par deux soignants indépendants dès
   cette phase, ou est-ce raisonnablement reporté au pilote ?
2. Sur un corpus synthétique, quel volume de cas réels annotés faudrait-il pour
   que les chiffres deviennent opposables ?
3. L'exposition systématique du désaccord entre le modèle et la règle est-elle la
   bonne ergonomie à l'accueil, ou risque-t-elle de créer du bruit ?
4. Le taux de désaccord modèle/règle est-il un indicateur de dérive acceptable en
   production, en l'absence d'étiquettes ?
