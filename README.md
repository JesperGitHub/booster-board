# Booster Board

Every 6 hours GitHub asks Discord who boosts the server, removes bad names
and saves them in `boosters.txt`. The VRChat world reads that file.

- `allow.txt`        names that got hidden by mistake (one per line)
- `hidden.txt`       names you never want to show
- `badwords.txt`     words hidden anywhere in a name
- `badwords_short.txt` short words hidden only as a separate word
- `safewords.txt`    normal words that contain a bad word (hancock, therapist...)

Boosters can hide themselves by getting the Discord role `hide-from-board`.
Run it right now: Actions tab > Update boosters > Run workflow.
