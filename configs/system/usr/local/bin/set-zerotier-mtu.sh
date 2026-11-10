#!/bin/bash
ZT_INTERFACE="ztmjfm7igv"
MTU_VALUE=1500

if ip link show $ZT_INTERFACE &>/dev/null; then
    ip link set dev $ZT_INTERFACE mtu $MTU_VALUE
fi
