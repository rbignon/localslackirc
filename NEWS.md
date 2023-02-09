Differences with localslackirc
==============================

This project is a fork of [localslackirc](https://github.com/ltworf/localslackirc).

New features
------------

- display more information at connection
```
Welcome to the Slack Server Teamname, romain!
Your host is domain.slack.com, running version localslackirc-2.0
There are 125 users and 30 bots on 1 server
5 Slack Workspace Admins
105 channels formed
```
- rename user's nickname at connection if not using the right one
```
You're now known as romain
```
- use the slack domain (`*.slack.com`) instead of `gethostname` for server name and user hostnames
- more information in WHOIS:
```
romain [U03D36HJC14@domain.slack.com]
 ircname  : Romain Bignon
          : Developer
 account  : romain@example.org
 account  : +33123456789
 account  : https://avatars.slack-edge.com/2022-10-27/XXXX_original.png
 channels : #general #random
 server   : domain.slack.com [TeamName]
 away     : My Status
          : Workspace Owner
 idle     : 0 days 23 hours 39 mins 19 secs
End of WHOIS
```
- if I send a message to a user who is away, I get a `RPL_AWAY` message
- add `--thread-replies` parameter to display thread answers in IM/channels instead of creating a new channel
- handle threads in IM
- correctly handle edited/deleted messages in IM
- fix `/me` messages
- `USERHOST` displays away/admin flags
- handle `CAP` command (currently empty)
- handle `JOIN :` command sent by recent irssi versions during connection (I don't know why, but if it doesn't get the right answer it freezes)
- fix crash in some case during shutdown or user disconnection
- handle slack connection errors
- handle `MODE` with parameters to display an error
- handle `MODE +b` to display an emply banlist
- add command `WHOWAS`
- concatenate multilines topic
- use colored logs
- handle groups and MPIM join/parts
- correctly deal with leaves of group/MPIM channels
- do not crash if the process is gracefully killed
- when a message is sent from the bot, use its name as sender instead of `bot`
- handle channels creation/rename/archive/delete
- display modes on channels:
  * `+p`: private
  * `+g`: group
  * `+i`: IM conversation
- display user modes:
  * `+a`: admin
  * `+o`: owner
* when we answer in a conversation, mark it as read


Internal changes
----------------

- move `*.py` files into a new `localslackirc` module (requires to use `python -m localslackirc` to run it locally, it will need to change packaging to create a `localslackirc` script)
- split `irc.py` into `daemon.py` and `irc.py`
- do not use bytes anymore
- rework of the commands handling
- use `logging` module instead of syslog
- do not crash if an exception is uncatched in a command handler, just display an error to user and display the backtrace in logs
- flake8 fixes
