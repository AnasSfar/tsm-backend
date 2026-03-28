# 🔍 Audit de Déploiement Public - tsm-backend

Date: 28 Mars 2026  
État: ⚠️ **Nécessite corrections critiques**

---

## 📋 Résumé Exécutif

Cet audit identifie **5 problèmes critiques de sécurité** et **7 gaps documentaires** avant déploiement public.

### Verdict
- **Sécurité**: ❌ Non prêt (secrets exposés)
- **Documentation**: ⚠️ Partielle (installation manquante)
- **Dépendances**: ❌ Non documentées
- **GitHub**: ✅ Workflow CI présent
- **Code Quality**: ✅ Tests présents (Apple Music)

---

## 🚨 PROBLÈMES CRITIQUES (à fixer immédiatement)

### 1. Secrets dans le repo
**Sévérité**: 🔴 CRITIQUE

| Fichier | Contenu | Status |
|---------|---------|--------|
| `.env` | R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET, UPLOAD_TO_R2 | ❌ **Commité** |
| `collectors/apple_music/tools/json/apple_music_token.json` | JWT token Apple Music | ❌ **Commité** |

**Action requise:**
1. Ajouter au `.gitignore`:
   ```
   .env
   .env.local
   .env.*.local
   collectors/apple_music/tools/json/apple_music_token.json
   ```
2. Récupérer l'historique git avec `git filter-branch` ou `git-filter-repo`
3. Marquer comme secret sur GitHub (Settings > Secrets and variables)

---

### 2. Fichiers temporaires/caches commités
**Sévérité**: 🟠 HAUTE

| Pattern | Compte | Status |
|---------|--------|--------|
| `.log` (Spotify, browser cache logs) | 200+ | ❌ **Commis** |
| browser cache / chrome profiles | ✓ Multiple | ❌ **Commis** |
| `.backfill_browser_cache/` | ✓ Arborescence entière | ❌ **Commis** |
| `.pyc`, `__pycache__/` | ✓ (mais .gitignore existe) | ✅ Ignoré |

**Action requise:**
```gitignore
# Logs
*.log
run_daily.log
run_update_streams.log

# Browser caches
.backfill_browser_cache/
*/chrome_profile/
*/browser_cache/
```

---

### 3. Dépendances Non Documentées
**Sévérité**: 🟠 HAUTE

**Manquant:**
- ❌ `requirements.txt` ou `pyproject.toml`
- ❌ Python version spécifiée (testé: 3.13)
- ❌ Dépendances listées quelque part

**Dépendances détectées** (à partir du code):
```
requests>=2.28
urllib3>=1.26
playwright >= 1.30
playwright (para headless browser)
boto3 >= 1.26  (AWS S3/R2)
python-dotenv >= 0.19
BeautifulSoup4 (possible)
lxml (possible)
PIL/Pillow
```

---

### 4. Documentation Insuffisante
**Sévérité**: 🟠 HAUTE

**Manquant:**
- ❌ README principal (que Apple Music tests)
- ❌ Architecture overview
- ❌ Installation guide
- ❌ Environment setup
- ❌ Running collectors
- ❌ Deployment instructions

**Existant:**
- ✅ `.github/workflows/apple-music-tests.yml` (CI)
- ✅ `collectors/apple_music/README.md` (partiel)

---

### 5. Pas de Licence
**Sévérité**: 🟡 MOYEN

- ❌ Pas de `LICENSE` fichier
- ❌ Pas de mention dans README

**Recommandation:** Ajouter MIT ou Apache 2.0

---

## ⚠️ GAPS DE CONFIGURATION

### Manquant: `.env.example`
```env
# Cloudflare R2 (https://developers.cloudflare.com/r2/)
R2_ACCOUNT_ID=your_account_id
R2_ACCESS_KEY_ID=your_access_key
R2_SECRET_ACCESS_KEY=your_secret_key
R2_BUCKET=your_bucket_name

# Optional: Auto-upload to R2 after collect
UPLOAD_TO_R2=0

# Optional: Apple Music HTTP tuning
APPLE_MUSIC_TIMEOUT=20
APPLE_MUSIC_RETRY_TOTAL=3
APPLE_MUSIC_RETRY_BACKOFF=1.0

# Optional: Spotify/Charts countries
APPLE_MUSIC_COUNTRIES=fr,us,gb,de,au
```

### Manquant: `setup.py` (pour `pip install -e .`)
```python
from setuptools import setup, find_packages

setup(
    name="tsm-backend",
    version="1.0.0",
    description="Taylor Swift Music data collectors and exporters",
    packages=find_packages(),
    python_requires=">=3.11",
    install_requires=[
        "requests>=2.28",
        "urllib3>=1.26",
        "playwright>=1.30",
        "boto3>=1.26",
        "python-dotenv>=0.19",
    ],
)
```

---

## 📊 Checklist de Correction

### Immédiat (avant commit)
- [ ] Supprimer `.env` du repo (BFG repo-cleaner / git filter-repo)
- [ ] Supprimer `apple_music_token.json` du repo
- [ ] Mettre à jour `.gitignore`
- [ ] Supprimer `.log` et `browser_cache` du repo

### Court terme (avant release)
- [ ] Créer `requirements.txt`
- [ ] Créer `setup.py` ou `pyproject.toml`
- [ ] Créer `.env.example`
- [ ] Améliorer `README.md` (architecture, install, usage)
- [ ] Ajouter `LICENSE`
- [ ] Ajouter `CONTRIBUTING.md`

### Moyen terme (avant production)
- [ ] Ajouter secrets management (GitHub Secrets ou similar)
- [ ] Ajouter tests pour Spotify/Billboard
- [ ] Ajouter linting (black, flake8, ruff)
- [ ] Ajouter pre-commit hooks
- [ ] Docker Dockerfile (optionnel)

### Sécurité continue
- [ ] Scan des dépendances (dependabot)
- [ ] Secret scanning (GitHub)
- [ ] Code scanning (GitHub)

---

## 📁 Structure de Projet (Recommandée)

```
tsm-backend/
├── README.md                    # ✅ Incomplet, à améliorer
├── LICENSE                      # ❌ Manquant
├── CONTRIBUTING.md              # ❌ Manquant
├── DEPLOYMENT_AUDIT.md          # ✅ (ce fichier)
├── .env.example                 # ❌ Manquant
├── .gitignore                   # ✅ Partiel, à compléter
├── .github/
│   └── workflows/
│       └── apple-music-tests.yml # ✅ Présent
├── requirements.txt             # ❌ Manquant
├── setup.py                     # ❌ Manquant
├── collectors/
│   ├── apple_music/
│   │   ├── README.md
│   │   ├── run_apple_music.py
│   │   ├── tests/
│   │   │   └── test_http.py
│   │   └── core/
│   ├── spotify/
│   └── billboard/
├── scripts/                     # ✅ Présent (export, R2)
├── db/                          # ✅ Données (à documenter)
├── website/                     # ✅ Frontend static
└── docs/                        # ❌ Manquant (architecture, API docs)
```

---

## 🔐 Recommandations de Sécurité

1. **Secrets Management**
   - Utiliser GitHub Secrets pour CI
   - Utiliser HashiCorp Vault pour production
   - Jamais committer `.env` réel

2. **Credentials Rotation**
   - R2 keys: rotate tous les 6 mois
   - Apple Music token: auto-refresh à partir du web (déjà fait)

3. **Accès**
   - Repo: Private (tant que secrets present)
   - R2 bucket: limiter à IPs/roles spécifiques
   - GitHub: 2FA requis pour admins

---

## 📝 Prochaines Étapes

**Phase 1 (Maintenant):**
1. Générer les fichiers manquants (`.env.example`, `requirements.txt`, `setup.py`, `LICENSE`)
2. Nettoyer git history des secrets
3. Mettre à jour `.gitignore`

**Phase 2 (Cette semaine):**
1. Compléter README
2. Ajouter CONTRIBUTING.md
3. Ajouter GitHub secret scanning

**Phase 3 (Avant release):**
1. Tests pour tous collectors
2. Docker / deployment docs
3. API documentation
4. Code review interne

