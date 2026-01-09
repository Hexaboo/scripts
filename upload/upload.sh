#!/bin/bash

if [[ ! -f "$HOME/.env" ]]; then
    echo "❌ $HOME/.env file not found!"
    exit 1
fi
set -a
source $HOME/.env
set +a

for cmd in curl jq stat md5sum sha1sum du; do
    command -v "$cmd" >/dev/null || { echo "Missing: $cmd"; exit 1; }
done

usage() {
    echo "Usage: $0 [-A | -P | -G | -R | -S] -F <file>"
    exit 1
}

SERVICES=()
while getopts ":APGRSF:" opt; do
    case $opt in
        A) SERVICES=("PixelDrain" "GoFile" "Ranoz" "SourceForge") ;;
        P) SERVICES=("PixelDrain") ;;
        G) SERVICES=("GoFile") ;;
        R) SERVICES=("Ranoz") ;;
        S) SERVICES=("SourceForge") ;;
        F) FILE="$OPTARG" ;;
        *) usage ;;
    esac
done

[[ ! -f "$FILE" ]] && echo "❌ File not found: $FILE" && exit 1
[[ ${#SERVICES[@]} -eq 0 ]] && usage

NAME=$(basename "$FILE")
SIZE=$(du -h "$FILE" | cut -f1)
SIZE_B=$(stat -c %s "$FILE")
MD5=$(md5sum "$FILE" | cut -d ' ' -f1)
SHA1=$(sha1sum "$FILE" | cut -d ' ' -f1)
NOW=$(date "+%Y-%m-%d %H:%M:%S")

RESULTS=()


upload_pixeldrain() {
    echo "📤 PixelDrain..."
    R=$(curl --progress-bar -u ":$PIXELDRAIN_API_KEY" -F "file=@\"$FILE\"" https://pixeldrain.com/api/file)
    echo
    ID=$(echo "$R" | jq -r '.id')
    [[ "$ID" == "null" ]] && echo "❌ PixelDrain failed" && return
    LINK="https://pixeldrain.com/u/$ID"
    echo "✅ $LINK"
    RESULTS+=("*PixelDrain:* $LINK")
}

upload_gofile() {
    echo "📤 GoFile..."
    if [ -z "$GOFILE_TOKEN" ]; then
    R=$(curl -# -F "file=@\"$FILE\"" "https://upload.gofile.io/uploadFile")
    else
    R=$(curl -# -F "token=$GOFILE_TOKEN" -F "file=@\"$FILE\"" "https://upload.gofile.io/uploadFile")
    fi
    echo
    LINK=$(echo "$R" | jq -r '.data.downloadPage')
    [[ "$LINK" == http* ]] || { echo "❌ GoFile failed"; return; }
    echo "✅ $LINK"
    RESULTS+=("*GoFile:* $LINK")
}

upload_ranoz() {
    echo "📤 Ranoz.gg..."
    M=$(curl -s -X POST https://ranoz.gg/api/v1/files/upload_url \
        -H "Content-Type: application/json" \
        -d "{\"filename\":\"$NAME\",\"size\":$SIZE_B}")
    URL=$(echo "$M" | jq -r '.data.upload_url')
    LINK=$(echo "$M" | jq -r '.data.url')
    [[ "$URL" == "null" ]] && echo "❌ Ranoz init failed" && return

    curl --progress-bar -X PUT "$URL" --upload-file "$FILE" \
         -H "Content-Length: $SIZE_B" -o /dev/null
    echo
    echo "✅ $LINK"
    RESULTS+=("*Ranoz.gg:* $LINK")
}

upload_sourceforge() {
    echo "📤 SourceForge..."
    DEST="${SOURCEFORGE_USER}@frs.sourceforge.net:${SOURCEFORGE_PATH}/$NAME"

    if command -v pv >/dev/null; then
        pv "$FILE" | ssh "$DEST" true
    else
        echo "(Install pv for progress bar)"
        scp "$FILE" "$DEST"
    fi

    [[ $? -eq 0 ]] || { echo "❌ SF failed"; return; }

    LINK="https://sourceforge.net/projects/$SOURCEFORGE_PROJECT/files/$NAME/download"
    echo "✅ $LINK"
    RESULTS+=("*SourceForge:* $LINK")
}

send_tg() {
    MSG="✅ *Upload Complete*

📄 *File:* \`$NAME\`
📦 *Size:* $SIZE
🔐 *MD5:* \`$MD5\`
🔐 *SHA1:* \`$SHA1\`
🕒 *Time:* $NOW

$(printf "%s\n" "${RESULTS[@]}")"

    curl -s -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT_ID" \
        -d parse_mode="Markdown" \
        -d text="$MSG" >/dev/null

    echo "📩 Telegram sent."
}

for SVC in "${SERVICES[@]}"; do
    case $SVC in
        PixelDrain) upload_pixeldrain ;;
        GoFile) upload_gofile ;;
        Ranoz) upload_ranoz ;;
        SourceForge) upload_sourceforge ;;
    esac
    echo ""
done

send_tg
