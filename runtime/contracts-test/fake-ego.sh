#!/bin/sh
cat >/dev/null
if [ "$SCENARIO" = lost ]; then sleep 3; fi
if [ "$SCENARIO" = outgoing ]; then printf '%s\n' 'DRIVER-RESULT {"ok":true,"threadVerified":true,"reply":"【转交给:other】需要专家","chatUrl":"https://mock.invalid/thread/1"}'; exit 0; fi
printf '%s\n' 'DRIVER-RESULT {"ok":true,"threadVerified":true,"reply":"mock reply","chatUrl":"https://mock.invalid/thread/1"}'
