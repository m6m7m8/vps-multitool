#!/usr/bin/env bash
# Пошаговый установщик VPS Multitool v2 для новичков

set -euo pipefail

APP_DIR="/opt/vps-multitool"
REPO_URL="${MULTITOOL_REPO_URL:-https://github.com/m6m7m8/vps-multitool.git}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
err()   { echo -e "${RED}[ERR]${NC} $*" >&2; }

check_root() {
    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
        err "Запустите установку от имени root: sudo bash install.sh"
        exit 1
    fi
}

prepare_repo_dir() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo "")"

    if [[ -n "${script_dir}" && -f "${script_dir}/docker-compose.yaml" && -d "${script_dir}/bot" ]]; then
        WORK_DIR="${script_dir}"
        return 0
    fi

    WORK_DIR="${APP_DIR}"
    if [[ ! -d "${WORK_DIR}/bot" || ! -f "${WORK_DIR}/docker-compose.yaml" ]]; then
        info "Скачиваю файлы VPS Multitool в ${WORK_DIR}..."
        if ! command -v git >/dev/null 2>&1; then
            apt-get update -qq && apt-get install -y -qq git >/dev/null
        fi
        rm -rf "${WORK_DIR}"
        git clone --depth 1 "${REPO_URL}" "${WORK_DIR}"
    fi
}

install_base_tools_and_docker() {
    if [[ -f /etc/os-release ]]; then
        # shellcheck source=/dev/null
        source /etc/os-release
        info "ОС: ${PRETTY_NAME:-$ID}"
    fi

    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        info "Устанавливаю базовые пакеты (curl, git, python3)..."
        apt-get update -qq
        apt-get install -y -qq curl git python3 ca-certificates >/dev/null
    fi

    if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
        info "Устанавливаю Docker и Docker Compose..."
        curl -fsSL https://get.docker.com | sh
        systemctl enable --now docker >/dev/null 2>&1 || service docker start || true
    fi

    local swap_mb
    swap_mb=$(awk '/SwapTotal/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)
    if [[ "${swap_mb}" -lt 1500 && ! -f /swapfile ]]; then
        info "Создаю 2 ГБ swap-файл для стабильной работы дешёвого VPS..."
        fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
        chmod 600 /swapfile
        mkswap /swapfile >/dev/null
        swapon /swapfile || true
        grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    fi
    ok "Сервер и Docker готовы."
}

validate_tg_token() {
    local token="$1"
    python3 - "$token" <<'PYEOF'
import json, sys, urllib.request
token = sys.argv[1].strip()
if not token or ":" not in token:
    sys.exit(1)
try:
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
        if data.get("ok"):
            print(data["result"].get("username", ""))
            sys.exit(0)
except Exception:
    pass
sys.exit(1)
PYEOF
}

validate_gemini_api_key() {
    local key="$1"
    python3 - "$key" <<'PYEOF'
import sys, urllib.request
key = sys.argv[1].strip()
if not key:
    sys.exit(1)
try:
    url = f"https://generativelanguage.googleapis.com/v1beta/models?pageSize=1&key={key}"
    with urllib.request.urlopen(url, timeout=10) as r:
        if r.status == 200:
            sys.exit(0)
except Exception:
    pass
sys.exit(1)
PYEOF
}

prompt_wizard() {
    echo ""
    echo -e "${BOLD}=================================================${NC}"
    echo -e "${BOLD}   Настройка VPS Multitool Bot (4 простых шага)${NC}"
    echo -e "${BOLD}=================================================${NC}"
    echo ""

    # [1/4] BOT_TOKEN
    BOT_USERNAME=""
    if [[ -n "${BOT_TOKEN:-}" ]]; then
        BOT_USERNAME=$(validate_tg_token "${BOT_TOKEN}" || true)
        if [[ -z "${BOT_USERNAME}" ]]; then
            err "Переданный BOT_TOKEN не прошёл проверку Telegram API."
            exit 1
        fi
        ok "[1/4] Подключен бот @${BOT_USERNAME}"
    else
        while true; do
            read -rp $'\033[1m[1/4] Вставьте токен бота от @BotFather:\033[0m ' BOT_TOKEN </dev/tty
            BOT_TOKEN="${BOT_TOKEN//[[:space:]]/}"
            BOT_USERNAME=$(validate_tg_token "${BOT_TOKEN}" || true)
            if [[ -n "${BOT_USERNAME}" ]]; then
                ok "Токен валиден! Подключен бот: @${BOT_USERNAME}"
                break
            fi
            warn "Не удалось проверить токен (опечатка или нет ответа от api.telegram.org). Попробуйте ещё раз."
        done
    fi

    # [2/4] ADMIN_IDS или одноразовый BIND_CODE
    BIND_CODE="${BIND_CODE:-}"
    if [[ -z "${ADMIN_IDS:-}" ]]; then
        echo ""
        read -rp $'\033[1m[2/4] Ваш Telegram ID (или нажмите Enter — привяжем кодом прямо в боте):\033[0m ' ADMIN_IDS </dev/tty
        ADMIN_IDS="${ADMIN_IDS//[[:space:]]/}"
    fi

    if [[ -n "${ADMIN_IDS}" && ! "${ADMIN_IDS}" =~ ^-?[0-9]+(,-?[0-9]+)*$ ]]; then
        warn "ID должен быть числом. Создаю одноразовый код привязки вместо ID."
        ADMIN_IDS=""
    fi

    if [[ -z "${ADMIN_IDS}" ]]; then
        BIND_CODE=$(python3 -c 'import secrets; print(f"{secrets.randbelow(900000)+100000}")')
        ok "Сгенерирован одноразовый код привязки: ${BOLD}${BIND_CODE}${NC}"
    else
        ok "Вайтлист настроен на ID: ${ADMIN_IDS}"
    fi

    # [3/4] INITIAL_PIN
    if [[ -z "${INITIAL_PIN:-}" && -t 0 ]]; then
        echo ""
        while true; do
            read -rsp $'\033[1m[3/4] Придумайте PIN/пароль для бота (Enter = без пароля):\033[0m ' INITIAL_PIN </dev/tty
            echo ""
            if [[ -z "${INITIAL_PIN}" ]]; then
                info "PIN-замок выключен (можно включить потом в меню «🛡 Безопасность»)."
                break
            elif [[ "${#INITIAL_PIN}" -lt 4 ]]; then
                warn "Минимум 4 символа (или нажмите Enter, чтобы пропустить)."
            else
                ok "PIN-замок будет включён (3 ошибки -> блок на 15 мин)."
                break
            fi
        done
    fi

    # [4/4] GEMINI_API_KEY
    if [[ -z "${GEMINI_API_KEY:-}" ]]; then
        echo ""
        echo -e "Бесплатный ключ Gemini можно взять на: ${CYAN}https://aistudio.google.com/apikey${NC}"
        while true; do
            read -rp $'\033[1m[4/4] Вставьте Gemini API Key (или Enter — зададите позже в боте):\033[0m ' GEMINI_API_KEY </dev/tty
            GEMINI_API_KEY="${GEMINI_API_KEY//[[:space:]]/}"
            if [[ -z "${GEMINI_API_KEY}" ]]; then
                info "Ключ пропущен — добавите его в боте через «⚙️ Настройки ИИ»."
                break
            fi
            if validate_gemini_api_key "${GEMINI_API_KEY}"; then
                ok "Ключ Gemini проверен!"
                break
            fi
            warn "Не удалось проверить ключ (возможно, опечатка или IP сервера в стране без Gemini)."
            read -rp "Всё равно сохранить этот ключ? [y/N]: " keep_ans </dev/tty
            if [[ "${keep_ans}" =~ ^[YyДд]$ ]]; then
                break
            fi
        done
    fi
}

write_env_and_start() {
    mkdir -p "${WORK_DIR}/data"
    cat > "${WORK_DIR}/.env" <<EOF
BOT_TOKEN=${BOT_TOKEN}
ADMIN_IDS=${ADMIN_IDS:-}
BIND_CODE=${BIND_CODE:-}
INITIAL_PIN=${INITIAL_PIN:-}
GEMINI_API_KEY=${GEMINI_API_KEY:-}
GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.8-flash}
DB_PATH=/app/data/multitool.sqlite3
EOF
    chmod 600 "${WORK_DIR}/.env"

    info "Собираю и запускаю контейнер бота..."
    (cd "${WORK_DIR}" && docker compose up -d --build)

    sleep 2
    if ! docker ps --format '{{.Names}}' | grep -qx 'multitool-bot'; then
        err "Контейнер multitool-bot не запустился. Последние логи:"
        (cd "${WORK_DIR}" && docker compose logs --tail 30)
        exit 1
    fi
}

install_cli() {
    cat > /usr/local/bin/multitool <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "${WORK_DIR}"
case "\${1:-status}" in
    status)  docker compose ps ;;
    logs)    docker compose logs -f --tail 100 ;;
    restart) docker compose restart ;;
    stop)    docker compose down ;;
    update)  git pull --ff-only && docker compose up -d --build ;;
    reconfig) bash "${WORK_DIR}/install.sh" ;;
    *)
        echo "Использование: multitool {status|logs|restart|stop|update|reconfig}"
        exit 1
        ;;
esac
EOF
    chmod +x /usr/local/bin/multitool
}

print_summary() {
    echo ""
    echo -e "${GREEN}${BOLD}=================================================${NC}"
    echo -e "${GREEN}${BOLD}   Бот успешно запущен!${NC}"
    echo -e "${GREEN}${BOLD}=================================================${NC}"
    echo -e "Бот в Telegram : ${CYAN}@${BOT_USERNAME}${NC}"
    if [[ -n "${BIND_CODE:-}" ]]; then
        echo -e "Код привязки   : ${YELLOW}${BOLD}${BIND_CODE}${NC}  <-- откройте @${BOT_USERNAME} и отправьте этот код!"
    fi
    if [[ -n "${INITIAL_PIN:-}" ]]; then
        echo -e "PIN-замок      : ${GREEN}включён${NC} (введите ваш PIN после входа)"
    fi
    echo ""
    echo "Полезные команды на сервере:"
    echo "  multitool status   — статус контейнера"
    echo "  multitool logs     — логи бота в реальном времени"
    echo "  multitool reconfig — пройти настройку заново"
    echo "================================================="
}

main() {
    check_root
    prepare_repo_dir
    install_base_tools_and_docker
    prompt_wizard
    write_env_and_start
    install_cli
    print_summary
}

main "$@"
