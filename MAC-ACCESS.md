# Reach your Infinity OS console from a Mac

Infinity OS runs on your server. Its console listens **only on the server itself**
(`127.0.0.1:3103`). It has **no login page, no password and no HTTPS of its own.** You reach it from
your Mac through an **SSH tunnel**: an encrypted connection that makes the server's console appear
at an address on your own Mac.

**Your SSH key is your login.** Anyone who holds the private key on your Mac, or any other key
listed on the server, can open the console. Nobody else can. That is the whole security model, so
parts 1 and 5 matter.

You need: a Mac, the server's IP address, and the install user's name (`infinity` if you followed
`INSTALL.md`). Every command below is typed into **Terminal** on your Mac unless it says "on the
server". To open Terminal: press Command-Space, type `Terminal`, press Return.

---

## Part 1. Make an SSH key, if you have none

Check first:

```bash
ls ~/.ssh/id_ed25519.pub
```

**If it prints the file name**, you already have a key. Skip to part 2.

**If it prints `No such file or directory`**, make one:

```bash
ssh-keygen -t ed25519 -C "my-mac"
```

Press Return to accept the file location. **Then type a passphrase** (nothing appears on screen as
you type; that is normal), press Return, and type it again. Store it in your password manager. The
passphrase protects the key if your Mac is ever lost or copied.

So you are not asked for the passphrase every time, add the key to the Mac's keychain once:

```bash
ssh-add --apple-use-keychain ~/.ssh/id_ed25519
```

## Part 2. Put your public key on the server

Copy your **public** key (the file ending `.pub`; it is safe to share):

```bash
pbcopy < ~/.ssh/id_ed25519.pub
```

It is now on your clipboard, one line starting `ssh-ed25519`.

**When you create the server** (for example in the Hetzner Cloud Console), paste it into the
**SSH keys** field. The server then accepts your key for `root`, and `INSTALL.md` step 0 copies it
to the install user.

**If the server already exists without your key**, whoever can already log in to it adds the line,
**on the server, as the install user**:

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
nano ~/.ssh/authorized_keys      # paste the line at the end, then Control-O, Return, Control-X
chmod 600 ~/.ssh/authorized_keys
```

**Check it works** from your Mac (use the real IP):

```bash
ssh infinity@<server-ip> 'whoami'
```

The very first time, SSH asks `Are you sure you want to continue connecting (yes/no/[fingerprint])?`.
Type `yes` and press Return. **Expect:** `infinity`.

## Part 3. Before your first tunnel: check the browser terminal is off

The console has a built-in browser terminal. **A tunnel arrives at the server as a local
connection, so if that terminal were on, the tunnel would give a shell on your server to anyone who
can use the tunnel.** `INSTALL.md` step 9 switches it off. Confirm it, from your Mac:

```bash
ssh infinity@<server-ip> 'systemctl --user show brain-console.service -p Environment'
```

**Expect:** one line containing **both** `CONSOLE_TERMINAL=off` and `BRAIN_PG_DB=brain`. The second
proves you are reading the console's own settings.

**If `CONSOLE_TERMINAL=off` is not there, do not open the tunnel.** Do `INSTALL.md` step 9's first
block on the server, restart the console
(`systemctl --user restart brain-console.service`), and check again.

Repeat this check after every update (`UPDATING.md` step 7 does it for you).

## Part 4. Open the console

**The one command**, in Terminal on your Mac:

```bash
ssh -N -L 13103:127.0.0.1:3103 infinity@<server-ip>
```

**It looks as if nothing happens, and the window seems stuck. That is correct:** the tunnel is open
for as long as that command keeps running. Leave that Terminal window open.

**The one address**, in your browser:

**<http://127.0.0.1:13103/queue?tier=decide>**

To close the tunnel, click into that Terminal window and press Control-C. If your Mac sleeps or
changes network, the tunnel ends; run the command again.

**Confirm you are looking at your own console**, not something else on your Mac. On the server, run
`git -C ~/infinity-os rev-parse --short HEAD`, then open
<http://127.0.0.1:13103/api/health> in the browser. The `commit_at_start` value in that page must be
the same id. A page that loads is not enough on its own.

(The Mac side uses port `13103` rather than `3103` so it never collides with anything else on your
Mac listening on `3103`.)

## Part 5. A shortcut for tomorrow

So you do not have to remember the IP and the ports, save them once. In Terminal:

```bash
touch ~/.ssh/config && chmod 600 ~/.ssh/config
open -e ~/.ssh/config
```

TextEdit opens. Add these lines at the end, with your server's IP, then save and close:

```
Host infinity
    HostName <server-ip>
    User infinity
    LocalForward 13103 127.0.0.1:3103
    ServerAliveInterval 30
```

From now on, **the command is**:

```bash
ssh -N infinity
```

and the address is the same, <http://127.0.0.1:13103/queue?tier=decide>. Bookmark it.

`ServerAliveInterval 30` keeps the tunnel from being dropped by a quiet network.

## Part 6. Keeping it safe

- **Never open the console to the internet.** Do not set `CONSOLE_HOST=0.0.0.0` and do not add a
  firewall rule for port 3103. The tunnel is the only door, and it is locked with your key.
- **Do not use `tailscale serve` for the console.** It currently answers `403` on writes, so the
  tunnel is the supported route.
- **Lost or replaced Mac:** from another machine that can log in, remove the lost Mac's line from
  `~/.ssh/authorized_keys` on the server (the line ends with the name you gave in part 1, such as
  `my-mac`). Until you do, that key still opens your console.
- **Only add keys for people you would give a shell to.** A key that can open the tunnel can also
  log in to the server.

## When it does not work

| What you see | What it means | What to do |
|---|---|---|
| `Permission denied (publickey)` | The server does not have your public key for that user, or you typed the wrong user | Part 2, and check the user name |
| `bind [127.0.0.1]:13103: Address already in use` | A tunnel is already open in another Terminal window | Use that one, or close it with Control-C first |
| The browser says it cannot connect to `127.0.0.1` | The tunnel is not running | Run the command in part 4 or 5 and leave it open |
| In the tunnel window: `channel 2: open failed: connect failed: Connection refused` | The tunnel works but the console is not running on the server | On the server: `systemctl --user status brain-console.service` and send the output |
| The page loads but `commit_at_start` is not your server's commit | You are looking at a different console | Close every tunnel, open one again, re-check |
