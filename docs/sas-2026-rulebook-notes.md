# SAS CTF 2026 rulebook integration notes

Source reviewed on 2026-10-20: `SAS CTF Attack-Defence Rulebook 2026.pdf`.
This document records control-plane requirements, not challenge knowledge.

## Confirmed main-game protocol

- Organizer access is through six WireGuard client configurations.
- Each team receives three servers. The primary vulnbox is
  `10.80.<TEAM_ID>.2`; the other two are reached through the private team subnet
  and routed through the primary vulnbox.
- The checksystem and private scoreboard are at `http://10.60.20.2/`.
- Rounds last 120 seconds.
- Per service and round, the order is CHECK, then PUT if CHECK passed, then GET
  one previous unexpired flag.
- Statuses are OK, DOWN, MUMBLE, CORRUPT, and CHECK_FAILED.
- Flags are valid for 10 minutes, or five rounds after PUT.
- Flags match `[A-Z0-9]{31}=`.
- ForcAD accepts a JSON array with `PUT /flags` and the team token in the
  `X-Team-Token` header.
- A team can submit a stolen flag only while its corresponding own service is
  UP.
- Attack data is at `/api/client/attack_data` and has the shape
  `{task_name: {team_ip: [hints...]}}`.
- Some checkers will introduce additional behavior patterns or inputs during
  the game while retaining earlier behavior. Patch regression suites must grow
  monotonically rather than replacing old cases.

## Operational implications

1. The flag broker must use a 600-second expiry, validate the official regex,
   prioritize nearly expiring flags, and understand ForcAD response messages.
2. Flag farming and submission must be suppressed for a service whenever our
   corresponding service is not UP. Defense recovery has immediate offensive
   value.
3. Attack-data polling should replace entity enumeration. Exploit farms must
   not spray targets or create unnecessary traffic.
4. The monitor must distinguish availability, business-logic failure, and flag
   loss because DOWN, MUMBLE, and CORRUPT need different incident packets.
5. Checker and patch regression evidence must be retained throughout the game
   to survive newly introduced samples.
6. The primary vulnbox is a router as well as a service host. Deployment and
   rollback tooling must preserve routing and WireGuard state.

## Rules enforced by operator policy

- Never attack organizer infrastructure.
- Never inject fake flags into opponent services.
- Never use primitive DoS, flooding, or unnecessary high-volume traffic.
- Never filter opponents by network or timing statistics.
- Do not implement behavioral checker fingerprinting or blanket antifraud
  filtering. Fix exploit primitives while preserving legitimate behavior.

## Match schedule

- 11:00: archive passwords, server SSH key, and six WireGuard configs released.
- 11:30: network opens; checking, flags, and SLA scoring start.
- 18:30: scoreboard freezes, but live operations continue.
- 19:30: game ends.

The 30 minutes from 11:00 to 11:30 are the confirmed grace period. Arena's
default grace profile is therefore 1800 seconds.

## Special services

### Shellcode Arena

This has changing round rules and requires a separate service adapter after the
detailed mechanics are released. Do not force it into the ordinary flag-store
workflow before those rules are known.

### Back to the roots

This service is isolated on organizer laptops. Internet, LLMs, external
communications, personal devices, phones, and removable media are prohibited.
S4DFarm, Packmate, and Wireshark are preinstalled. One player may use the laptop
at a time, with changes only at 30-minute boundaries. Arena and all agents must
exclude this service. A returning player may brief teammates only from memory.

## Strategy facts

- LLMs are explicitly allowed for the ordinary main game.
- The unpatched, non-attacking NPC is team 15. The first valid flag stolen from
  NPC for each service earns First Blood and SAS coins.
- Score is computed per service as SLA multiplied by flag points. Losing SLA
  weakens both defense and the ability to submit flags.
