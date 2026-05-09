---
title: "Fixing Erlang/OTP SSH Pre-Authentication RCE (CVE-2025-32433 Pattern)"
category: "security/vulnerability-patching"
domain: "erlang-otp-ssh"
tags:
  - erlang
  - otp
  - ssh
  - cve
  - pre-auth-rce
  - state-machine
  - gen_statem
  - protocol-security
applicability:
  - Erlang/OTP SSH server vulnerabilities where protocol messages bypass authentication
  - State machine bugs allowing out-of-order message processing
  - Any SSH implementation where connection-layer messages are handled before auth completes
---

# Fixing Erlang/OTP SSH Pre-Authentication Remote Code Execution

## Overview

This skill covers identifying and patching vulnerabilities in the Erlang/OTP SSH server where SSH connection protocol messages (RFC 4254) are processed before the client has completed authentication (RFC 4252). The canonical example is CVE-2025-32433, but the pattern applies to any state-machine bypass in `ssh_connection_handler.erl`.

## Background: SSH Protocol Phases

The SSH protocol has strict ordering:

1. **Transport Layer (RFC 4253)**: Key exchange, encryption negotiation
2. **User Authentication (RFC 4252)**: Client proves identity
3. **Connection Protocol (RFC 4254)**: Channels, exec requests, port forwarding

Connection protocol messages (channel open, channel request, etc.) MUST only be processed after successful authentication. If a server processes them earlier, an attacker can execute commands without credentials.

## High-Level Workflow

1. **Identify the state machine file**: In Erlang/OTP SSH, the core state machine lives in `lib/ssh/src/ssh_connection_handler.erl`. This is a `gen_statem` callback module.

2. **Understand the state progression**: The states flow as:
   - `{hello, Role}` → `{kexinit, Role}` → `{key_exchange, Role}` → `{new_keys, Role}` → `{ext_info, Role}` → `{service_request, Role}` → `{userauth, Role}` → `{connected, Role}`
   - Only `{connected, _}` and `{ext_info, _}` (post-auth) are "authenticated" states.

3. **Find the connection message dispatcher**: Search for `conn_msg` or `CONNECTION_MSG` — this is how connection protocol messages are routed internally after being decoded from the wire.

4. **Check if the handler guards on state**: The vulnerability exists when `handle_event(internal, {conn_msg, Msg}, StateName, State)` does NOT verify that `StateName` is an authenticated state before processing.

5. **Apply the fix**: Add a guard clause BEFORE the existing handler that rejects `{conn_msg, _}` events when the state is not authenticated, sending `SSH_DISCONNECT_PROTOCOL_ERROR`.

6. **Verify**: Confirm that (a) normal SSH still works, (b) pre-auth connection messages are rejected, and (c) the code compiles cleanly.

## Step-by-Step Implementation

### Step 1: Locate the State Machine

```bash
# Find the main SSH connection handler
find /app/workspace -name "ssh_connection_handler.erl" -type f
# Typically at: lib/ssh/src/ssh_connection_handler.erl
```

### Step 2: Identify the CONNECTED Macro

```bash
# Find the macro that defines "authenticated" states
grep -n "CONNECTED" /app/workspace/otp_src_*/lib/ssh/src/ssh_connection_handler.erl | head -20
```

You'll find something like:

```erlang
-define(CONNECTED(StateName),
        (element(1,StateName) == connected orelse
         element(1,StateName) == ext_info ) ).
```

This macro returns `true` only when the connection is in a post-authentication state.

### Step 3: Find the Vulnerable Handler

```bash
# Search for the conn_msg handler
grep -n "conn_msg" /app/workspace/otp_src_*/lib/ssh/src/ssh_connection_handler.erl
```

Look for a `handle_event` clause like:

```erlang
handle_event(internal, {conn_msg, Msg}, StateName, #data{...} = D0) ->
    %% Processes connection protocol messages
    ...
```

The vulnerability is that this clause matches ANY `StateName` — it doesn't check authentication status.

### Step 4: Identify the Disconnect Macro

```bash
# Find how disconnects are sent
grep -n "send_disconnect" /app/workspace/otp_src_*/lib/ssh/src/ssh_connection_handler.erl | head -10
```

The macro is typically:

```erlang
?send_disconnect(Code, DetailedText, StateName, D)
```

And the relevant disconnect code constant:

```bash
grep -n "SSH_DISCONNECT_PROTOCOL_ERROR\|PROTOCOL_ERROR" /app/workspace/otp_src_*/lib/ssh/src/ssh.hrl
```

Usually defined as:

```erlang
-define(SSH_DISCONNECT_PROTOCOL_ERROR, 2).
```

### Step 5: Apply the Fix

The fix adds a new `handle_event` clause that matches `{conn_msg, _}` when NOT in an authenticated state, placed BEFORE the existing handler (Erlang pattern matching is order-dependent):

```erlang
%%% --- Pre-auth connection message rejection (CVE-2025-32433 fix) ---
handle_event(internal, {conn_msg, _Msg}, StateName, 
             #data{ssh_params = #ssh{role = server}} = D) 
  when not ?CONNECTED(StateName) ->
    {stop, {shutdown, "Protocol error: connection message before authentication"},
     ?send_disconnect(?SSH_DISCONNECT_PROTOCOL_ERROR,
                      ?fmt("Connection protocol message received before authentication in state ~p", 
                           [StateName]),
                      StateName, D)};
```

### Step 6: Verify the Fix Location

The new clause must appear BEFORE the existing `{conn_msg, Msg}` handler. In the original code, if the existing handler is at (for example) line 766, the new clause goes at line 759.

## Critical Notes

### Erlang Pattern Matching Order

In Erlang, function clauses are tried top-to-bottom. The new guard clause MUST appear before the general `{conn_msg, Msg}` handler. If placed after, it will never match because the general clause catches everything first.

### The `?send_disconnect` Macro Return Value

The `?send_disconnect` macro in OTP SSH returns the state data needed for the `{stop, ...}` tuple. The exact pattern is:

```erlang
{stop, {shutdown, Reason}, ?send_disconnect(Code, Text, StateName, D)}
```

Some versions use a slightly different return shape — check the existing disconnect calls in the file to match the local convention.

### Server-Side Only

The guard includes `#ssh{role = server}` to ensure this only applies to the server side. Client connections have different state semantics.

### What NOT to Do

- Do NOT modify the message decoding/dispatch layer — that would break the protocol parser
- Do NOT add the check inside the existing handler body — use a separate clause for clarity and to ensure it's checked first
- Do NOT disconnect with `SSH_DISCONNECT_BY_APPLICATION` — use `SSH_DISCONNECT_PROTOCOL_ERROR` (code 2) per RFC 4253 §11.1

## Common Pitfalls

1. **Placing the clause after the existing handler**: Erlang matches top-to-bottom. The guard clause must come FIRST.

2. **Forgetting the `when not ?CONNECTED(StateName)` guard**: Without the guard, you'd block ALL connection messages including legitimate post-auth ones.

3. **Not checking `role = server`**: The fix should only apply server-side. Client-side state machines have different flows.

4. **Using the wrong disconnect code**: RFC 4253 specifies `SSH_DISCONNECT_PROTOCOL_ERROR` (2) for protocol violations. Using other codes may confuse clients or fail compliance tests.

5. **Trying to fix at the transport/decode layer**: The messages are correctly decoded — the bug is in the state machine's willingness to process them at the wrong time. Fix it at the state machine level.

6. **Modifying `ssh_message.erl` or `ssh_transport.erl`**: These files handle encoding/decoding. The vulnerability is in the handler logic, not in parsing.

## Understanding the Attack Vector

The exploit works by:

1. Completing the SSH transport layer (key exchange) — this is unauthenticated
2. Immediately sending `SSH_MSG_CHANNEL_OPEN` (byte 90) to open a session channel
3. Sending `SSH_MSG_CHANNEL_REQUEST` (byte 98) with request type "exec" and the command payload
4. The server processes these because the `{conn_msg, Msg}` handler doesn't check state

A typical exploit in Python using Paramiko's transport layer:

```python
import paramiko
from paramiko.message import Message

# Connect and complete key exchange only (no auth)
transport = paramiko.Transport((host, port))
transport.connect()  # Key exchange only

# Craft SSH_MSG_CHANNEL_OPEN
msg = Message()
msg.add_byte(chr(90))  # SSH_MSG_CHANNEL_OPEN
msg.add_string("session")
msg.add_int(0)  # sender channel
msg.add_int(32768)  # initial window size
msg.add_int(32768)  # max packet size
transport._send_message(msg)

# Craft SSH_MSG_CHANNEL_REQUEST with exec
msg = Message()
msg.add_byte(chr(98))  # SSH_MSG_CHANNEL_REQUEST
msg.add_int(0)  # recipient channel
msg.add_string("exec")
msg.add_boolean(True)  # want reply
msg.add_string("id")  # command to execute
transport._send_message(msg)
```

## File Structure Reference

```
otp_src_XX.Y.Z/
└── lib/
    └── ssh/
        └── src/
            ├── ssh_connection_handler.erl  ← FIX GOES HERE (gen_statem)
            ├── ssh_connection.erl          ← Connection protocol logic
            ├── ssh_message.erl             ← Message encode/decode
            ├── ssh_transport.erl           ← Transport layer
            ├── ssh_auth.erl               ← Authentication
            └── ssh.hrl                    ← Constants/macros
```

## Reference Implementation

This is the complete, end-to-end patch that fixes CVE-2025-32433 in Erlang/OTP SSH. The patch targets `lib/ssh/src/ssh_connection_handler.erl`.

```erlang
%% =============================================================================
%% COMPLETE FIX FOR CVE-2025-32433: Pre-Authentication RCE in Erlang/OTP SSH
%% =============================================================================
%%
%% File: lib/ssh/src/ssh_connection_handler.erl
%%
%% CONTEXT: The file contains a gen_statem implementation. The relevant section
%% is in the handle_event/4 callbacks. Find the existing handler:
%%
%%   handle_event(internal, {conn_msg, Msg}, StateName, #data{...} = D0) ->
%%       ...existing connection message processing...
%%
%% INSERT THE FOLLOWING CLAUSE *IMMEDIATELY BEFORE* THAT HANDLER:
%% =============================================================================

%%--------------------------------------------------------------------
%% Reject connection protocol messages received before authentication.
%% CVE-2025-32433: An attacker could send SSH_MSG_CHANNEL_OPEN and
%% SSH_MSG_CHANNEL_REQUEST before authenticating, achieving RCE.
%%
%% The ?CONNECTED(StateName) macro returns true only for:
%%   - {connected, Role}
%%   - {ext_info, Role}
%% All other states (hello, kexinit, key_exchange, new_keys, 
%% service_request, userauth) are pre-authentication.
%%--------------------------------------------------------------------
handle_event(internal, {conn_msg, _Msg}, StateName,
             #data{ssh_params = #ssh{role = server}} = D) 
  when not ?CONNECTED(StateName) ->
    {stop, {shutdown, "Protocol error: connection message before authentication"},
     ?send_disconnect(?SSH_DISCONNECT_PROTOCOL_ERROR,
                      ?fmt("Connection protocol message received before authentication in state ~p",
                           [StateName]),
                      StateName, D)};

%% --- EXISTING HANDLER (already in the file, do NOT modify) ---
%% handle_event(internal, {conn_msg, Msg}, StateName, #data{...} = D0) ->
%%     ...
```

### Applying as a Patch File

```diff
--- a/lib/ssh/src/ssh_connection_handler.erl
+++ b/lib/ssh/src/ssh_connection_handler.erl
@@ -756,6 +756,14 @@ handle_event(internal, {adjust_window, ChannelId, Bytes}, StateName, D) ->
     end;
 
 
+%% Reject connection protocol messages before authentication (CVE-2025-32433)
+handle_event(internal, {conn_msg, _Msg}, StateName,
+             #data{ssh_params = #ssh{role = server}} = D)
+  when not ?CONNECTED(StateName) ->
+    {stop, {shutdown, "Protocol error: connection message before authentication"},
+     ?send_disconnect(?SSH_DISCONNECT_PROTOCOL_ERROR,
+                      ?fmt("Connection protocol message received before authentication in state ~p",
+                           [StateName]),
+                      StateName, D)};
+
 handle_event(internal, {conn_msg, Msg}, StateName, #data{starter = User,
                                                          connection_state = Cs0,
                                                          event_queue = Qev0,
```

### Shell Script to Apply the Fix Programmatically

```bash
#!/bin/bash
# apply_cve_2025_32433_fix.sh
# Usage: ./apply_cve_2025_32433_fix.sh /path/to/otp_src_XX.Y.Z

OTP_DIR="${1:-.}"
FILE="$OTP_DIR/lib/ssh/src/ssh_connection_handler.erl"

if [ ! -f "$FILE" ]; then
    echo "ERROR: Cannot find $FILE"
    exit 1
fi

# Find the line number of the existing conn_msg handler
LINE=$(grep -n "^handle_event(internal, {conn_msg, Msg}" "$FILE" | head -1 | cut -d: -f1)

if [ -z "$LINE" ]; then
    echo "ERROR: Cannot find conn_msg handler in $FILE"
    exit 1
fi

echo "Found existing conn_msg handler at line $LINE"

# Check if fix is already applied
if grep -q "conn_msg, _Msg.*not.*CONNECTED" "$FILE"; then
    echo "Fix already applied."
    exit 0
fi

# Create the patch text
PATCH_TEXT='%% Reject connection protocol messages before authentication (CVE-2025-32433)
handle_event(internal, {conn_msg, _Msg}, StateName,
             #data{ssh_params = #ssh{role = server}} = D)
  when not ?CONNECTED(StateName) ->
    {stop, {shutdown, "Protocol error: connection message before authentication"},
     ?send_disconnect(?SSH_DISCONNECT_PROTOCOL_ERROR,
                      ?fmt("Connection protocol message received before authentication in state ~p",
                           [StateName]),
                      StateName, D)};

'

# Insert the patch before the existing handler
{
    head -n $((LINE - 1)) "$FILE"
    echo "$PATCH_TEXT"
    tail -n +$LINE "$FILE"
} > "${FILE}.patched"

mv "${FILE}.patched" "$FILE"

echo "Fix applied successfully at line $LINE of $FILE"

# Verify
if grep -q "conn_msg, _Msg.*StateName" "$FILE"; then
    echo "Verification: Fix is present in the file."
else
    echo "WARNING: Verification failed. Check the file manually."
    exit 1
fi
```

### Verification Script

```bash
#!/bin/bash
# verify_fix.sh — Checks that the fix is correctly placed

OTP_DIR="${1:-.}"
FILE="$OTP_DIR/lib/ssh/src/ssh_connection_handler.erl"

echo "=== Checking fix presence ==="
if grep -q "conn_msg, _Msg" "$FILE" && grep -q "not.*CONNECTED" "$FILE"; then
    echo "✓ Guard clause for pre-auth conn_msg rejection found"
else
    echo "✗ Fix not found!"
    exit 1
fi

echo ""
echo "=== Checking clause ordering ==="
GUARD_LINE=$(grep -n "conn_msg, _Msg" "$FILE" | head -1 | cut -d: -f1)
HANDLER_LINE=$(grep -n "conn_msg, Msg}" "$FILE" | grep -v "_Msg" | head -1 | cut -d: -f1)

if [ "$GUARD_LINE" -lt "$HANDLER_LINE" ]; then
    echo "✓ Guard clause (line $GUARD_LINE) appears before handler (line $HANDLER_LINE)"
else
    echo "✗ WRONG ORDER: Guard at $GUARD_LINE, handler at $HANDLER_LINE"
    exit 1
fi

echo ""
echo "=== Checking CONNECTED macro exists ==="
if grep -q "define(CONNECTED" "$FILE"; then
    echo "✓ CONNECTED macro defined"
    grep "define(CONNECTED" "$FILE"
else
    echo "✗ CONNECTED macro not found — check if it's in a header file"
fi

echo ""
echo "=== Checking disconnect code ==="
if grep -A2 "conn_msg, _Msg" "$FILE" | grep -q "SSH_DISCONNECT_PROTOCOL_ERROR"; then
    echo "✓ Uses SSH_DISCONNECT_PROTOCOL_ERROR"
else
    echo "✗ Wrong or missing disconnect code"
    exit 1
fi

echo ""
echo "All checks passed."
```

## Decision Criteria: When to Apply This Pattern

Apply this fix pattern when:
- The Erlang/OTP version is < 27.3.3 (or < 26.2.5.11 for OTP 26, < 25.3.2.20 for OTP 25)
- The `ssh_connection_handler.erl` has a `{conn_msg, Msg}` handler without a state guard
- You need to maintain backward compatibility (no API changes, just add a guard clause)

Do NOT apply if:
- The version already has the fix (check for `conn_msg, _Msg` with `not ?CONNECTED`)
- The SSH server is disabled or not exposed to untrusted networks (though fixing is still recommended)

## Testing the Fix

After applying:

1. **Normal SSH should work**: `ssh user@host` with valid credentials should succeed
2. **Pre-auth exploit should fail**: The exploit script should get disconnected immediately
3. **Build should succeed**: `cd lib/ssh && erlc -I include src/ssh_connection_handler.erl` (or full OTP build)

The test typically verifies:
- `test_ssh_works`: Normal authenticated SSH session functions correctly
- `test_exploit`: Pre-auth channel open/exec attempt is rejected (connection closed)
- `test_build`: The patched source compiles without errors