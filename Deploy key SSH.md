# Deploy key (SSH) — pull private repos on the VPS

Keep the GitHub repo **private**. Give the VPS read-only access with a deploy key so you do **not** need to make the repo public to `git pull`.

---

## 1. On the VPS (once)

```bash
# Create a key (once)
ssh-keygen -t ed25519 -C "vps-augusta" -f ~/.ssh/github_deploy -N ""

# Use it for GitHub
cat >> ~/.ssh/config << 'EOF'
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/github_deploy
  IdentitiesOnly yes
EOF

chmod 600 ~/.ssh/config ~/.ssh/github_deploy

# Show the public key — copy this whole line
cat ~/.ssh/github_deploy.pub
```

---

## 2. On GitHub

1. Repo → **Settings** → **Deploy keys** → **Add deploy key**
2. Title: `vps-contabo`
3. Paste the `.pub` line from the VPS
4. Leave **Allow write access** unchecked (pull only)
5. Save

Do this on each private repo you deploy (e.g. Augusta-Australia, TradeCyrus-App).  
A deploy key is tied to **one repo**. For several repos, either:

- add the **same** public key as a deploy key on each repo, or  
- add the key once under your GitHub user: **Settings → SSH and GPG keys** (works for all your repos)

---

## 3. Test + pull (Augusta)

```bash
ssh -T git@github.com
# expect: Hi ... You've successfully authenticated...

cd /home/ragadmin/ragadmin/projects/Augusta-Australia
git remote set-url origin git@github.com:nsaqib238/Augusta-Australia.git
git pull origin main
```

---

## 4. TradeCyrus (same pattern)

```bash
cd /var/www/tradecyrus   # or your TradeCyrus path on the VPS
git remote set-url origin git@github.com:nsaqib238/TradeCyrus-App.git
git pull origin main
```

Add a deploy key on the TradeCyrus-App repo first (if you did not add the key to your GitHub user account).

---

## Notes

- After this, `git pull` works while the repo stays private.
- Never commit the private key (`~/.ssh/github_deploy`) — only the `.pub` goes on GitHub.
- If SSH fails, check: `ssh -vT git@github.com` and confirm the deploy key is on the correct repo.
