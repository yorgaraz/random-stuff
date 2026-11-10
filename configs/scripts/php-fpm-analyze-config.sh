#!/bin/bash

# Configuration
PHP_VERSION="8.1"
CONF_DIR="/opt/plesk/php/${PHP_VERSION}/etc/php-fpm.d"
SERVICE_NAME="plesk-php${PHP_VERSION/./}-fpm"
LOG_DIR="/var/log/plesk-php81-fpm-extra"

# Markers to identify our block safely
BLOCK_START="; BEGIN METRICS - DO NOT EDIT MANUALLY"
BLOCK_END="; END METRICS"

# ensure_log_dir: Sets up the folder and permissions
ensure_log_dir() {
    if [ ! -d "$LOG_DIR" ]; then
        mkdir -p "$LOG_DIR"
        echo "Created log directory: $LOG_DIR"
    fi
    # 1777 = Sticky bit (like /tmp). Everyone can write, only owner can delete.
    chmod 1777 "$LOG_DIR"
    # Ensure the parent directory is traversable
    chmod o+x "$(dirname "$LOG_DIR")"
}

# create_config_block: Writes the config to a temp file
create_config_block() {
    cat <<EOF > /tmp/php_metrics_block.conf
$BLOCK_START
access.log = $LOG_DIR/\$pool.access.log
access.format = "%R %u [%t] \"%m %{REQUEST_URI}e\" %s %{mili}d %{kilo}M %C%%"
slowlog = $LOG_DIR/\$pool.slow.log
request_slowlog_timeout = 1s
$BLOCK_END
EOF
}

enable_metrics() {
    ensure_log_dir
    create_config_block
    
    echo "Enabling metrics in $CONF_DIR..."
    
    for file in "$CONF_DIR"/*.conf; do
        [ -f "$file" ] || continue
        
        # 1. Clean existing block (Idempotency check)
        # Deletes everything between START and END inclusive
        sed -i "/$BLOCK_START/,/$BLOCK_END/d" "$file"
        
        # 2. Inject new block
        # Finds 'pm =' (which exists in all pools) and reads our temp file after it
        sed -i "/^pm =/r /tmp/php_metrics_block.conf" "$file"
        
        echo "Updated $file"
    done
    
    # Verify Syntax
    echo "Testing configuration..."
    /opt/plesk/php/${PHP_VERSION}/sbin/php-fpm -t
    
    if [ $? -eq 0 ]; then
        echo "Config OK. Restarting Service..."
        systemctl restart "$SERVICE_NAME"
        echo "SUCCESS: Metrics enabled."
    else
        echo "CRITICAL ERROR: Config test failed! Check $CONF_DIR manually."
        # Optional: Restore logic could go here, but usually manual intervention is safer on fail
    fi
    
    rm /tmp/php_metrics_block.conf
}

disable_metrics() {
    echo "Disabling metrics in $CONF_DIR..."
    
    for file in "$CONF_DIR"/*.conf; do
        [ -f "$file" ] || continue
        
        # Delete the block if it exists
        if grep -q "$BLOCK_START" "$file"; then
            sed -i "/$BLOCK_START/,/$BLOCK_END/d" "$file"
            echo "Cleaned $file"
        fi
    done
    
    echo "Restarting Service..."
    systemctl restart "$SERVICE_NAME"
    echo "SUCCESS: Metrics disabled."
}

# Main Switch
case "$1" in
    enable)
        enable_metrics
        ;;
    disable)
        disable_metrics
        ;;
    *)
        echo "Usage: $0 {enable|disable}"
        exit 1
        ;;
esac

