#!/bin/sh
# Prüft, ob private Angaben (z. B. echter Name, private Adressen) im Repository landen.
#
# Die Suchmuster stehen bewusst NICHT im Repository (sie würden sich sonst selbst verraten):
#   - lokal:   eine Zeile pro Muster (erweiterter regulärer Ausdruck, ohne Groß-/Kleinschreibung) in
#              ${XDG_CONFIG_HOME:-$HOME/.config}/hass-adhstrack/verbotene-muster.txt
#   - GitLab:  CI/CD-Variable VERBOTENE_MUSTER (Muster durch | getrennt)
# Ausgegeben werden nur Fundstellen (Datei/Commit), nie die Muster selbst.
#
# Aufruf:
#   privatsphaere_pruefen.sh index                 # vorgemerkte Änderungen + Autor (pre-commit)
#   privatsphaere_pruefen.sh nachricht <datei>     # Commit-Nachricht (commit-msg)
#   privatsphaere_pruefen.sh historie [<rev>...]   # Inhalte, Autoren, Nachrichten, Tags (Standard: alle Refs)
set -eu

datei="${XDG_CONFIG_HOME:-$HOME/.config}/hass-adhstrack/verbotene-muster.txt"
muster="${VERBOTENE_MUSTER:-}"
if [ -z "$muster" ] && [ -f "$datei" ]; then
    muster=$(grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "$datei" | paste -s -d '|' -)
fi
if [ -z "$muster" ]; then
    echo "❌ Privatsphäre-Prüfung: keine Suchmuster gefunden ($datei bzw. VERBOTENE_MUSTER)." >&2
    exit 2
fi

fund=0
melde() { echo "❌ Private Angabe gefunden: $1" >&2; fund=1; }
trifft() { grep -q -i -E -e "$muster"; }

case "${1:-historie}" in
    index)
        git var GIT_AUTHOR_IDENT | trifft && melde "Commit-Autor (git config user.name / user.email)"
        git var GIT_COMMITTER_IDENT | trifft && melde "Committer (git config user.name / user.email)"
        for pfad in $(git diff --cached --name-only --diff-filter=ACMR); do
            echo "$pfad" | trifft && melde "Dateiname $pfad"
            git show ":$pfad" | trifft && melde "Datei $pfad"
        done
        ;;
    nachricht)
        trifft < "$2" && melde "Commit-Nachricht"
        ;;
    historie)
        shift || true
        if [ "$#" -eq 0 ]; then set -- --all; fi
        for commit in $(git rev-list "$@"); do
            git log -1 --format='%an%n%ae%n%cn%n%ce%n%B' "$commit" | trifft && melde "Autor/Nachricht von Commit $commit"
            git ls-tree -r --name-only "$commit" | trifft && melde "Dateiname in Commit $commit"
            dateien=$(git grep -I -l -i -E -e "$muster" "$commit" -- . 2>/dev/null || true)
            [ -n "$dateien" ] && melde "Inhalt in Commit $commit: $(echo "$dateien" | sed "s|^$commit:||" | paste -s -d ' ' -)"
        done
        for tag in $(git tag); do
            git cat-file -p "refs/tags/$tag" | trifft && melde "Tag $tag (Ersteller/Nachricht)"
        done
        ;;
    *)
        echo "Unbekannter Modus: $1" >&2
        exit 2
        ;;
esac

if [ "$fund" -ne 0 ]; then
    echo "   Bitte entfernen bzw. Autor setzen: git config user.name Sedrah87; git config user.email 19957615+Sedrah87@users.noreply.github.com" >&2
    exit 1
fi
echo "✅ Privatsphäre-Prüfung bestanden."
