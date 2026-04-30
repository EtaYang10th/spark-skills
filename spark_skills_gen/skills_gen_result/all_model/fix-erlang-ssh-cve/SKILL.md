---
id: fix-erlang-ssh-cve
title: Fix Erlang/OTP SSH Server Authentication Bypass Vulnerabilities
version: 1.0.0
tags: [erlang, otp, ssh, security, cve, authentication, fsm]
description: >
  Procedural guide for identifying and patching authentication bypass
  vulnerabilities in the Erlang/OTP SSH server. Covers CVE-class bugs where
  connection-layer messages (channel open, exec requests) are processed before
  authentication is complete, enabling unauthenticated remote code execution.
---

# Fix Erlang/OTP SSH Server Authentication Bypass Vulnerabilities

## Overview

The Erlang/OTP SSH server is implemented as a `gen_statem` finite state machine
in `lib/ssh/src/ssh_connection_handler.erl`. A class of critical vulnerabilities
arises when connection-layer SSH messages (e.g., `SSH_MSG_CHANNEL_OPEN`,
`SSH_MSG_CHANNEL_REQUEST exec`) are dispatched and processed regardless of the
current FSM state — including pre-authentication states like `{userauth, server}`.

This allows an attacker to open a channel and execute arbitrary commands without
ever authenticating.

The fix pattern is always the same: add a guard clause that rejects connection-
layer messages when the FSM is not in a post-authentication state.

---

## High-Level Workflow

### Step 1 — Orient Yourself in the Codebase

The SSH implementation lives entirely under `lib/ssh/src/`. The files you care
about most are:

| File | Role |
|---|---|
| `ssh_connection_handler.erl` | FSM — the main target for auth-bypass fixes |
| `ssh_connection.erl` | Connection-layer message handling logic |
| `ssh_transport.erl` | Packet decode/encode, message routing |
| `ssh_auth.erl` | Authentication state machine |
| `ssh.hrl` | Record definitions (`#ssh{}`, `#ssh_msg_*{}`) |
| `ssh_connect.hrl` | Connection-layer message records |

```bash
find /app/workspace/otp_src_*/lib/ssh/src -type f -name "*.erl" | sort
```

### Step 2 — Identify the FSM States and the Authentication Boundary

The FSM progresses through states in this order (server role):

```
{hello, server}
  -> {service_request, server}
    -> {userauth, server}          ← PRE-AUTH (danger zone)
      -> {ext_info, server, _}     ← POST-AUTH
        -> {connected, server}     ← POST-AUTH (normal operation)
```

The `?CONNECTED` macro defines the post-authentication states:

```erlang
-define(CONNECTED(StateName),
        (element(1,StateName) == connected orelse
         element(1,StateName) == ext_info ) ).
```

Any message processed in `{userauth, server}` or earlier is processed without
authentication. That is the vulnerability class.

### Step 3 — Trace How Connection-Layer Messages Are Dispatched

Search for the macro or pattern that routes SSH connection-layer messages into
the FSM:

```bash
grep -n "CONNECTION_MSG\|conn_msg\|?CONNECTION_MSG" \
  /app/workspace/otp_src_*/lib/ssh/src/ssh_connection_handler.erl | head -40
```

You will find something like:

```erlang
?CONNECTION_MSG(Msg) ->
    {next_event, internal, {conn_msg, Msg}};
```

This means ALL connection-layer messages (`#ssh_msg_channel_open{}`,
`#ssh_msg_channel_request{}`, etc.) are converted to `{conn_msg, Msg}` internal
events and dispatched to `handle_event/4` — with no state guard at the dispatch
site.

### Step 4 — Find the `conn_msg` Handler

```bash
grep -n "conn_msg" \
  /app/workspace/otp_src_*/lib/ssh/src/ssh_connection_handler.erl
```

Then read the handler:

```bash
# Adjust line numbers based on grep output above
sed -n '755,830p' /app/workspace/otp_src_*/lib/ssh/src/ssh_connection_handler.erl
```

The vulnerable handler looks like this — note the absence of any state guard:

```erlang
handle_event(internal, {conn_msg,Msg}, StateName,
             #data{connection_state = Connection0,
                   event_queue = Qev0} = D0) ->
    Role = ?role(StateName),
    Renegotiation = renegotiation(StateName),
    try ssh_connection:handle_msg(Msg, Connection0, Role, D0#data.ssh_params) of
        ...
```

This handler fires in ANY state, including `{userauth, server}`.

### Step 5 — Confirm the Attack Path

The attack sequence is:

1. TCP connect to SSH server
2. Complete SSH handshake (key exchange) — this is unauthenticated
3. Send `SSH_MSG_CHANNEL_OPEN` (type `session`) — no auth required
4. Send `SSH_MSG_CHANNEL_REQUEST` with `request_type = "exec"` and a command
5. Server executes the command as the process owner

Verify by checking `ssh_connection:handle_msg` for `#ssh_msg_channel_request{}`:

```bash
grep -n "channel_request\|exec\|handle_msg" \
  /app/workspace/otp_src_*/lib/ssh/src/ssh_connection.erl | head -30
```

### Step 6 — Apply the Fix

Insert a new `handle_event` clause **immediately before** the existing
`conn_msg` handler. This clause must:

- Match `{conn_msg, _Msg}` with a `when not ?CONNECTED(StateName)` guard
- Send a `SSH_DISCONNECT_PROTOCOL_ERROR` to the peer
- Stop the FSM

```erlang
%% --- INSERT THIS CLAUSE BEFORE THE EXISTING conn_msg HANDLER ---
handle_event(internal, {conn_msg,_Msg}, StateName, D0)
  when not ?CONNECTED(StateName) ->
    %% Connection-layer messages MUST NOT be processed before authentication
    %% is complete. Receiving them in a pre-auth state is a protocol violation.
    {Shutdown, D} =
        ?send_disconnect(?SSH_DISCONNECT_PROTOCOL_ERROR,
                         "Connection-layer message received before authentication",
                         StateName, D0),
    {stop, Shutdown, D};
%% --- EXISTING HANDLER FOLLOWS (unchanged) ---
handle_event(internal, {conn_msg,Msg}, StateName,
             #data{connection_state = Connection0,
                   event_queue = Qev0} = D0) ->
    ...
```

To apply this as a precise file edit, locate the exact line number of the
existing `conn_msg` handler and insert before it:

```bash
# Find the line
grep -n "handle_event(internal, {conn_msg,Msg}" \
  /app/workspace/otp_src_*/lib/ssh/src/ssh_connection_handler.erl
```

### Step 7 — Verify the Fix Is Syntactically Correct

Erlang clause ordering matters. The new guard clause must appear BEFORE the
general clause. Verify:

```bash
grep -n "conn_msg" \
  /app/workspace/otp_src_*/lib/ssh/src/ssh_connection_handler.erl
```

Expected output (line numbers will vary, but order must be preserved):

```
759: handle_event(internal, {conn_msg,_Msg}, StateName, D0)
760:   when not ?CONNECTED(StateName) ->
...
775: handle_event(internal, {conn_msg,Msg}, StateName,
```

### Step 8 — Verify the `?CONNECTED` Macro Covers All Post-Auth States

```bash
grep -A4 "define(CONNECTED" \
  /app/workspace/otp_src_*/lib/ssh/src/ssh_connection_handler.erl
```

Expected:

```erlang
-define(CONNECTED(StateName),
        (element(1,StateName) == connected orelse
         element(1,StateName) == ext_info ) ).
```

If the macro is different (e.g., only matches `connected`), you may need to
expand it to include `ext_info` as well, since `ext_info` is a valid
post-authentication state used during SSH extension negotiation.

---

## Concrete Executable Code

### Full Fix Patch (Erlang)

This is the complete new clause to insert. Adapt the `?send_disconnect` macro
call to match the exact macro signature used in the file:

```erlang
%% CVE fix: reject connection-layer messages before authentication completes.
%% This prevents unauthenticated channel open + exec attacks.
handle_event(internal, {conn_msg,_Msg}, StateName, D0)
  when not ?CONNECTED(StateName) ->
    {Shutdown, D} =
        ?send_disconnect(?SSH_DISCONNECT_PROTOCOL_ERROR,
                         "Connection-layer message received before authentication",
                         StateName, D0),
    {stop, Shutdown, D};
```

### Locating the Insertion Point with sed

```bash
FILE=/app/workspace/otp_src_*/lib/ssh/src/ssh_connection_handler.erl

# Find the line number of the existing conn_msg handler
LINE=$(grep -n "handle_event(internal, {conn_msg,Msg}" "$FILE" | head -1 | cut -d: -f1)
echo "Inserting before line $LINE"

# Preview context
sed -n "$((LINE-3)),$((LINE+5))p" "$FILE"
```

### Verifying the `?send_disconnect` Macro Signature

Before writing the fix, confirm the macro signature used elsewhere in the file:

```bash
grep -n "send_disconnect" \
  /app/workspace/otp_src_*/lib/ssh/src/ssh_connection_handler.erl | head -10
```

Common signatures:

```erlang
% 4-argument form (most common in OTP 27.x)
?send_disconnect(Code, Reason, StateName, D)

% 5-argument form (includes file/line info)
?send_disconnect(Code, Reason, _Detail, StateName, D)
```

Use whichever form is already used in the surrounding code.

### Checking for Similar Vulnerabilities in Other Message Types

After fixing `conn_msg`, check whether other internal event types have the same
problem:

```bash
grep -n "handle_event(internal," \
  /app/workspace/otp_src_*/lib/ssh/src/ssh_connection_handler.erl \
  | grep -v "when\|CONNECTED\|userauth\|hello\|service"
```

Look for handlers that:
1. Process sensitive operations (exec, subsystem, port-forward)
2. Have no state guard (`when StateName == ...` or `when ?CONNECTED(...)`)

---

## Common Pitfalls

### Pitfall 1 — Inserting the Guard Clause AFTER the General Clause

Erlang pattern matching is first-match. If the general `{conn_msg, Msg}` clause
appears before your new `{conn_msg, _Msg} when not ?CONNECTED(...)` clause, the
guard will never fire.

**Always insert the more-specific (guarded) clause BEFORE the general clause.**

### Pitfall 2 — Using the Wrong `?CONNECTED` Semantics

The `?CONNECTED` macro checks `element(1, StateName)`. States are tuples like
`{connected, server}` or `{ext_info, server, _}`. Do not compare `StateName`
directly to an atom — it won't match.

Wrong:
```erlang
when StateName =/= connected  % StateName is a tuple, not an atom
```

Right:
```erlang
when not ?CONNECTED(StateName)  % uses element(1, StateName)
```

### Pitfall 3 — Forgetting `ext_info` as a Valid Post-Auth State

`{ext_info, server, _}` is a legitimate post-authentication state used during
SSH extension negotiation (RFC 8308). If you only allow `connected`, you will
break extension negotiation for compliant clients.

The `?CONNECTED` macro in OTP 27.x already covers both. Verify it does before
relying on it.

### Pitfall 4 — Modifying `ssh_connection.erl` Instead of the Handler

`ssh_connection:handle_msg/4` is the right place to add per-message logic, but
it is the WRONG place to add the authentication gate. By the time `handle_msg`
is called, the message has already been accepted. The gate must be in
`ssh_connection_handler.erl` at the FSM event dispatch level.

### Pitfall 5 — Sending a Generic Error Instead of `SSH_DISCONNECT_PROTOCOL_ERROR`

Use `?SSH_DISCONNECT_PROTOCOL_ERROR` (code 2), not a generic disconnect. This
is the correct RFC 4253 disconnect reason for receiving a message out of
sequence. Using the wrong code may cause test validators to fail.

### Pitfall 6 — Not Stopping the FSM After Disconnect

The fix must return `{stop, Shutdown, D}`. Returning `{keep_state, D}` or
`{next_state, ...}` after sending a disconnect leaves the connection in an
inconsistent state and may allow further exploitation.

---

## Reference: SSH FSM State Transitions (Server Role)

```
TCP accept
    |
    v
{hello, server}
    | (version exchange complete)
    v
{service_request, server}
    | (SSH_MSG_SERVICE_REQUEST for "ssh-userauth")
    v
{userauth, server}          <-- PRE-AUTH: conn_msg MUST be rejected here
    | (authentication success)
    v
{ext_info, server, _}       <-- POST-AUTH: conn_msg allowed
    | (ext_info exchange done, or skipped)
    v
{connected, server}         <-- POST-AUTH: normal operation
```

The `?CONNECTED` macro returns `true` for `ext_info` and `connected` states.
All other states are pre-authentication and must not process connection-layer
messages.

---

## Reference: Relevant SSH Message Types

These are the connection-layer messages that become `{conn_msg, Msg}` events.
All of them are dangerous if processed pre-auth:

| Record | SSH Message | Risk |
|---|---|---|
| `#ssh_msg_channel_open{}` | `SSH_MSG_CHANNEL_OPEN` | Opens a session channel |
| `#ssh_msg_channel_request{}` | `SSH_MSG_CHANNEL_REQUEST` | Exec, shell, subsystem |
| `#ssh_msg_channel_data{}` | `SSH_MSG_CHANNEL_DATA` | Data to channel |
| `#ssh_msg_global_request{}` | `SSH_MSG_GLOBAL_REQUEST` | Port forwarding setup |

The attack chain is: `channel_open` → `channel_request{exec}` → RCE.

---

## Quick Checklist Before Submitting

- [ ] New guard clause is BEFORE the general `conn_msg` handler
- [ ] Guard uses `when not ?CONNECTED(StateName)` (not atom comparison)
- [ ] `?CONNECTED` macro covers both `connected` and `ext_info`
- [ ] Disconnect uses `?SSH_DISCONNECT_PROTOCOL_ERROR`
- [ ] FSM is stopped with `{stop, Shutdown, D}` after disconnect
- [ ] No other pre-auth message handlers were accidentally broken
- [ ] File compiles without syntax errors (check bracket/period balance)
