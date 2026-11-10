#!/bin/bash
# Usage: ./analyze.sh [-w width] [-n count] [-p log_path] [-s sort_by]

# --- Defaults ---
URL_WIDTH=50
NUM_ITEMS=20
LOG_DIR="/var/log/plesk-php81-fpm-extra"
SORT_COL=1       # Default sort by Hits (Column 1)
SORT_NAME="HITS" # Display name

# --- Parse Arguments ---
while getopts "w:n:p:s:h" opt; do
  case $opt in
    w) URL_WIDTH="$OPTARG" ;;
    n) NUM_ITEMS="$OPTARG" ;;
    p) LOG_DIR="$OPTARG" ;;
    s) 
       case "$OPTARG" in
           cpu)  SORT_COL=2; SORT_NAME="AVG CPU" ;;
           mem)  SORT_COL=3; SORT_NAME="AVG MEM" ;;
           dur)  SORT_COL=4; SORT_NAME="AVG DUR" ;;
           hits) SORT_COL=1; SORT_NAME="HITS" ;;
           *) echo "Invalid sort option. Use: cpu, mem, dur, hits"; exit 1 ;;
       esac
       ;;
    h) echo "Usage: $0 [-w width] [-n count] [-s cpu|mem|dur|hits]"; exit 0 ;;
    \?) echo "Invalid option -$OPTARG" >&2; exit 1 ;;
  esac
done

if [ ! -d "$LOG_DIR" ]; then
    echo "Error: Directory $LOG_DIR not found."
    exit 1
fi

echo "Parsing logs in $LOG_DIR..."
echo "Top $NUM_ITEMS items | Sorted by: $SORT_NAME | URL Width: $URL_WIDTH chars"
echo "-----------------------------------------------------------------------------------------------------------"
printf "%-30s | %-${URL_WIDTH}s | %-6s | %-8s | %-9s | %-8s\n" \
    "POOL (DOMAIN)" "URL (PATH)" "HITS" "AVG-CPU" "AVG-MEM" "AVG-MS"
echo "-----------------------------------------------------------------------------------------------------------"

# --- STEP 1: AGGREGATE ---
for logfile in "$LOG_DIR"/*.access.log; do
    [ -e "$logfile" ] || continue
    
    filename=$(basename "$logfile")
    poolname="${filename%.access.log}"
    poolname="${poolname%.log}"

    awk -v pool="$poolname" '
    BEGIN { min_duration = 0 }
    {
        # Fields from end of line
        cpu_val = $NF; gsub(/%/, "", cpu_val);
        mem_val = $(NF-1);
        dur_val = $(NF-2);
        
        # Find URL (starts with /)
        uri = "unknown";
        for(i=1; i<=NF; i++) {
             if($i ~ /^\//) {
                 gsub(/"/, "", $i);
                 split($i, uri_parts, "?"); 
                 uri = uri_parts[1];
                 break;
             }
        }
        if (dur_val < min_duration) next;

        key = pool "|" uri
        count[key]++
        sum_cpu[key] += cpu_val
        sum_mem[key] += mem_val
        sum_dur[key] += dur_val
    }
    END {
        for (k in count) {
            split(k, key_parts, "|");
            p = key_parts[1];
            u = key_parts[2];
            
            if (count[k] > 5 || sum_dur[k]/count[k] > 500) {
                avg_cpu = sum_cpu[k] / count[k]
                avg_mem = sum_mem[k] / count[k]
                avg_dur = sum_dur[k] / count[k]
                
                # Output raw intermediate data (Tab Separated)
                # Col 1: Hits
                # Col 2: CPU
                # Col 3: Mem
                # Col 4: Dur
                # Col 5: Pool
                # Col 6: URL (Encoded)
                printf "%d\t%.2f\t%.0f\t%.0f\t%s\t%s\n", \
                count[k], avg_cpu, avg_mem, avg_dur, p, u
            }
        }
    }' "$logfile"
done | \
# --- STEP 2: SORT (Dynamic Column) & DECODE ---
sort -rnk "$SORT_COL" | head -n "$NUM_ITEMS" | \
# Decode Greek characters (Bash Native)
while read -r line; do
    # Replace % with \x so echo -e interprets it as hex
    decoded_line=$(echo "$line" | sed 's/%/\\x/g')
    echo -e "$decoded_line"
done | \
# --- STEP 3: FORMAT TABLE ---
awk -F"\t" -v width="$URL_WIDTH" '
{
    # Input: $1=Hits, $2=CPU, $3=Mem, $4=Dur, $5=Pool, $6=URL
    hits=$1; cpu=$2; mem=$3; dur=$4; pool=$5; url=$6;
    
    # Truncate visually
    display_url = substr(url, 1, width);
    
    printf "%-30s | %-*s | %-6d | %-8.2f | %-9s | %-8s\n", \
    substr(pool, 1, 30), width, display_url, hits, cpu, mem, dur
}'
