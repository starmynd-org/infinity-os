# Infinity OS

**Infinity OS is a self-hosted runtime for work done by AI agents and the one person they work
for.** It stores sessions, events, recommendations, work items, runs and receipts in Postgres;
hands work to stateless one-shot agent processes; ranks what needs a human; and shows that queue
in a console on your own server. It never approves its own work: acceptance is a human act.

You install it on a Linux server you control, and you reach its console through an SSH tunnel.

## Status

**Alpha, pre-release.** The first public version will be `v0.01`. Until a release is tagged, the
newest commit on `main` is the only thing published, and it may change without notice.

What this version **does not have**, so nobody expects it:

- **No login page, no passwords, no user accounts.** The console trusts whoever can reach it.
- **No HTTPS and no public web access.** The console listens on `127.0.0.1` only. The supported way
  in is an SSH tunnel from your own computer (`MAC-ACCESS.md`).
- **One operator per install.** There is no multi-user mode.
- **No Data section** (sources, goals and KPIs). That is planned for a later version.
- **No hosted service.** Nothing runs anywhere but your server.

Read `docs/KNOWN-GAPS.md` before you rely on it. It lists real defects, measured.

## Get started

| To | Read |
|---|---|
| install it on a clean Linux server | `INSTALL.md` |
| open the console from a Mac | `MAC-ACCESS.md` |
| move an install to a newer release | `UPDATING.md` |
| understand how it is built | `docs/ARCHITECTURE.md` |
| run it day to day | `docs/OPERATING.md` |
| know why it behaves as it does | `docs/WHY-IT-IS-LIKE-THIS.md` |
| change it | `docs/CHANGING-IT.md` |

## Get help

- **A bug, or something that does not work as a guide says:** open an issue with the **Bug report**
  form. Include the commit you are on (`git rev-parse --short HEAD`) and the command and output that
  surprised you.
- **An idea or a request:** open an issue with the **Feature request** form.
- **A security problem:** do not open an issue. Follow `SECURITY.md`.

Never paste a password, key, token or anyone's personal data into an issue. The forms ask you to
confirm that before you submit.

Outside pull requests are not accepted yet; see `CONTRIBUTING.md`.
