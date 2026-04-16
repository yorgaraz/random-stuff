#!/usr/bin/env bash
set -e

show_help() {
    echo "Usage: $(basename "$0") [OPTIONS]"
    echo "Automatically detects and tears down orphaned Docker Compose projects."
    echo "An 'orphan' is a project whose containers are running, but the underlying docker-compose.yml is missing from the disk."
    echo ""
    echo "Options:"
    echo "  -p, --project <name>  Target specific project(s). Can be used multiple times."
    echo "  -v, --keep-volumes    Leave persistent volumes intact."
    echo "  -d, --dry-run         Preview what will be deleted without executing."
    echo "  -h, --help            Show this help menu."
    echo ""
    echo "Examples:"
    echo "  $(basename "$0")                     # Auto-detect all orphans and prompt to clean"
    echo "  $(basename "$0") -d                  # See what the auto-detect would clean up"
    echo "  $(basename "$0") -p ispy -p media    # Only clean up 'ispy' and 'media'"
}

TARGET_PROJECTS=()
KEEP_VOLUMES=0
DRY_RUN=0

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        -h|--help) show_help; exit 0 ;;
        -v|--keep-volumes) KEEP_VOLUMES=1 ;;
        -d|--dry-run) DRY_RUN=1 ;;
        -p|--project)
            if [ -n "$2" ] && [ ${2:0:1} != "-" ]; then
                TARGET_PROJECTS+=("$2")
                shift
            else
                echo "Error: Argument for $1 is missing" >&2
                exit 1
            fi
            ;;
        -*) echo "Unknown parameter passed: $1" >&2; exit 1 ;;
        *) echo "Unknown parameter passed: $1" >&2; exit 1 ;;
    esac
    shift
done

# --- Auto-Detect Orphans if no -p flags are passed ---
if [ ${#TARGET_PROJECTS[@]} -eq 0 ]; then
    echo "Scanning for orphaned Compose projects..."
    
    # Get all containers managed by compose
    CONTAINERS=$(docker ps -aq --filter "label=com.docker.compose.project")
    
    if [ -n "$CONTAINERS" ]; then
        for c in $CONTAINERS; do
            PROJ_NAME=$(docker inspect "$c" --format '{{index .Config.Labels "com.docker.compose.project"}}')
            CONF_FILES=$(docker inspect "$c" --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}')
            
            IS_ORPHAN=1
            # Handle multiple compose files separated by commas
            IFS=',' read -ra CONF_ARRAY <<< "$CONF_FILES"
            for file in "${CONF_ARRAY[@]}"; do
                if [ -f "$file" ]; then
                    IS_ORPHAN=0 # Found a valid file, not an orphan
                    break
                fi
            done
            
            if [ $IS_ORPHAN -eq 1 ]; then
                TARGET_PROJECTS+=("$PROJ_NAME")
            fi
        done
    fi
    
    # Deduplicate the array
    if [ ${#TARGET_PROJECTS[@]} -gt 0 ]; then
        mapfile -t TARGET_PROJECTS < <(printf "%s\n" "${TARGET_PROJECTS[@]}" | sort -u)
    fi
fi

# --- Execution Plan ---
if [ ${#TARGET_PROJECTS[@]} -eq 0 ]; then
    echo -e "\e[32mNo orphaned projects found. System is clean.\e[0m"
    exit 0
fi

echo -e "\n\e[33m--- TARGETED PROJECTS ---\e[0m"
printf "  - %s\n" "${TARGET_PROJECTS[@]}"

echo -e "\n\e[33m--- EXECUTION PLAN ---\e[0m"
declare -A CONTAINERS_TO_REMOVE
declare -A NETWORKS_TO_REMOVE
declare -A VOLUMES_TO_REMOVE

for PROJECT in "${TARGET_PROJECTS[@]}"; do
    C_LIST=$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")
    [ -n "$C_LIST" ] && CONTAINERS_TO_REMOVE["$PROJECT"]="$C_LIST"
    
    N_LIST=$(docker network ls -q --filter "label=com.docker.compose.project=$PROJECT")
    [ -n "$N_LIST" ] && NETWORKS_TO_REMOVE["$PROJECT"]="$N_LIST"
    
    if [ $KEEP_VOLUMES -eq 0 ]; then
        V_LIST=$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")
        [ -n "$V_LIST" ] && VOLUMES_TO_REMOVE["$PROJECT"]="$V_LIST"
    fi
    
    echo -e "\e[1;34mProject: $PROJECT\e[0m"
    if [ -n "$C_LIST" ]; then
        echo "  Containers:"
        docker inspect --format '    - {{.Name}} ({{.Id}})' $C_LIST | sed 's/ - \// - /'
    fi
    if [ -n "$N_LIST" ]; then
        echo "  Networks:"
        docker inspect --format '    - {{.Name}}' $N_LIST
    fi
    if [ -n "${VOLUMES_TO_REMOVE[$PROJECT]}" ]; then
        echo "  Volumes:"
        docker inspect --format '    - {{.Name}}' ${VOLUMES_TO_REMOVE[$PROJECT]}
    fi
    echo ""
done

if [ $DRY_RUN -eq 1 ]; then
    echo -e "\e[32m[Dry Run] Execution plan generated. Exiting.\e[0m"
    exit 0
fi

# --- Confirmation ---
read -p "WARNING: Proceed with teardown of the above resources? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
fi

# --- Teardown ---
for PROJECT in "${TARGET_PROJECTS[@]}"; do
    echo -e "\nCleaning up \e[1;34m$PROJECT\e[0m..."
    
    if [ -n "${CONTAINERS_TO_REMOVE[$PROJECT]}" ]; then
        echo "  -> Force removing containers..."
        set +e; docker rm -f ${CONTAINERS_TO_REMOVE[$PROJECT]} >/dev/null; set -e
    fi
    
    if [ -n "${NETWORKS_TO_REMOVE[$PROJECT]}" ]; then
        echo "  -> Removing networks..."
        set +e; docker network rm ${NETWORKS_TO_REMOVE[$PROJECT]} >/dev/null 2>&1; set -e
    fi
    
    if [ -n "${VOLUMES_TO_REMOVE[$PROJECT]}" ]; then
        echo "  -> Removing volumes..."
        set +e; docker volume rm ${VOLUMES_TO_REMOVE[$PROJECT]} >/dev/null; set -e
    fi
done

echo -e "\n\e[32mCleanup complete.\e[0m"

