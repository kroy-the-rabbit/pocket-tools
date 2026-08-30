#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Create the release signing key for this project and lay out what GitHub
# needs. Run once, on a machine you trust, then keep the backup somewhere the
# machine is not.
#
# A key of its own rather than the one that signs your commits: this half of it
# goes into a GitHub secret, where a compromise of the repository or of an
# action would otherwise reach your personal identity. Revoking a key that only
# ever signed releases costs a release; revoking the other one costs everything
# it ever signed.
set -euo pipefail

NAME="${KEY_NAME:-Pocket Cheats Release Signing}"
EMAIL="${KEY_EMAIL:-}"
YEARS="${KEY_YEARS:-3}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT_DIR:-$REPO/.release-key}"

if [[ -z "$EMAIL" ]]; then
  EMAIL="$(git -C "$REPO" config user.email 2>/dev/null || true)"
fi
if [[ -z "$EMAIL" ]]; then
  echo "set KEY_EMAIL, or configure git user.email" >&2
  exit 1
fi

if [[ -e "$REPO/KEYS" ]]; then
  echo "$REPO/KEYS already exists." >&2
  echo "Refusing to make a second key: the one people already imported is" >&2
  echo "the one that has to keep signing. Delete it deliberately if you are" >&2
  echo "really rotating." >&2
  exit 1
fi

mkdir -p "$OUT"
chmod 700 "$OUT"

echo "Creating '$NAME <$EMAIL>', expiring in ${YEARS}y."

# KEY_PASSPHRASE makes this unattended. Without it gpg asks, which is what you
# want when running this by hand. An earlier version tried batch mode first and
# fell back to prompting, which quietly produced a key with no passphrase at
# all whenever batch mode worked: the opposite of what it advertised.
if [[ -n "${KEY_PASSPHRASE:-}" ]]; then
  gpg --batch --pinentry-mode loopback --passphrase "$KEY_PASSPHRASE" \
      --quick-generate-key "$NAME <$EMAIL>" ed25519 sign "${YEARS}y"
else
  echo "You will be asked for a passphrase. Use one: this key leaves the machine."
  gpg --quick-generate-key "$NAME <$EMAIL>" ed25519 sign "${YEARS}y"
fi

FPR="$(gpg --list-keys --with-colons "$NAME" | awk -F: '/^fpr:/ {print $10; exit}')"
[[ -n "$FPR" ]] || { echo "could not find the key just created" >&2; exit 1; }

gpg --armor --export "$FPR" > "$REPO/KEYS"

# The private half, for the GitHub secret. Written to a file rather than
# printed, so it is not left in the terminal's scrollback.
if [[ -n "${KEY_PASSPHRASE:-}" ]]; then
  gpg --batch --pinentry-mode loopback --passphrase "$KEY_PASSPHRASE" \
      --armor --export-secret-keys "$FPR" | base64 -w0 > "$OUT/GPG_PRIVATE_KEY"
  printf '%s' "$KEY_PASSPHRASE" > "$OUT/GPG_PASSPHRASE"
else
  gpg --armor --export-secret-keys "$FPR" | base64 -w0 > "$OUT/GPG_PRIVATE_KEY"
fi
printf '%s' "$FPR" > "$OUT/GPG_KEY_ID"
chmod 600 "$OUT"/*

cat <<EOF

Key:          $FPR
Public half:  $REPO/KEYS   (commit this)
Private half: $OUT/        (never commit this; $OUT is git-ignored)

Set these three repository secrets:

  gh secret set GPG_PRIVATE_KEY < "$OUT/GPG_PRIVATE_KEY"
  gh secret set GPG_KEY_ID      < "$OUT/GPG_KEY_ID"
  gh secret set GPG_PASSPHRASE  < "$OUT/GPG_PASSPHRASE"

Then back up the private half offline and delete $OUT.
Publish the fingerprint somewhere that is not this repository, so that
somebody who distrusts the repository has a second place to check it.
EOF
