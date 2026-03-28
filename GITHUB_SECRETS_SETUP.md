# GitHub Secrets Setup

## Automated Setup (Recommended)

### Prerequisites
- GitHub CLI installed: https://cli.github.com/
- Authenticated with GitHub: `gh auth login`

### Steps

1. **Authenticate with GitHub:**
   ```powershell
   & "C:\Program Files\GitHub CLI\gh.exe" auth login
   ```

2. **Run the secrets setup script:**
   ```powershell
   python add_github_secrets.py
   ```

3. **Verify secrets are added:**
   ```powershell
   & "C:\Program Files\GitHub CLI\gh.exe" secret list
   ```

---

## Manual Setup (Alternative)

If automated setup fails, add secrets manually:

### Steps

1. **Go to GitHub Repository Settings:**
   - https://github.com/AnasSfar/tsm-backend/settings/secrets/actions

2. **Add Each Secret:**
   Click **"New repository secret"** for each:

   #### R2_ACCOUNT_ID
   - **Name:** `R2_ACCOUNT_ID`
   - **Value:** `2f1cadce2fc3c64b1f9936bf1b2272fc`
   - Click **Add secret**

   #### R2_ACCESS_KEY_ID
   - **Name:** `R2_ACCESS_KEY_ID`
   - **Value:** `8ad3cea9d0bb9bc5e6fb75c79d883372`
   - Click **Add secret**

   #### R2_SECRET_ACCESS_KEY
   - **Name:** `R2_SECRET_ACCESS_KEY`
   - **Value:** `f6c1a8ea4ccfc4d94cc645b2f0654a80db5414f26763fadfac8a07a1d2cf05f6`
   - Click **Add secret**

   #### R2_BUCKET
   - **Name:** `R2_BUCKET`
   - **Value:** `taylor-data`
   - Click **Add secret**

3. **Verify:**
   - All 4 secrets should appear in the secrets list

---

## What These Secrets Do

| Secret | Purpose | Used By |
|--------|---------|---------|
| `R2_ACCOUNT_ID` | Cloudflare R2 account identifier | CI/CD R2 uploads |
| `R2_ACCESS_KEY_ID` | API key for R2 authentication | CI/CD R2 uploads |
| `R2_SECRET_ACCESS_KEY` | Secret key for R2 authentication | CI/CD R2 uploads |
| `R2_BUCKET` | R2 bucket name where data is stored | CI/CD R2 uploads |

---

## Using Secrets in GitHub Actions

Once added, reference secrets in workflows with: `${{ secrets.SECRET_NAME }}`

Example (`.github/workflows/apple-music-tests.yml`):
```yaml
env:
  R2_ACCOUNT_ID: ${{ secrets.R2_ACCOUNT_ID }}
  R2_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY_ID }}
  R2_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_ACCESS_KEY }}
  R2_BUCKET: ${{ secrets.R2_BUCKET }}
```

---

## Security Notes

✅ **Never commit .env files**  
✅ **Use GitHub Secrets for sensitive data**  
✅ **Rotate credentials regularly**  
✅ **Restrict secret access to actions only**  
✅ **Audit secret usage in action logs**

Visit [GitHub Secrets Docs](https://docs.github.com/en/actions/security-guides/encrypted-secrets) for more info.
