# localslackirc
# Copyright (C) 2018-2022 Salvo "LtWorf" Tomaselli
#
# localslackirc is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# author Salvo "LtWorf" Tomaselli <tiposchi@tiscali.it>

import asyncio
from dataclasses import dataclass, field
import datetime
import json
import logging
from time import time
from typing import Literal, Optional, Any, NamedTuple, Sequence, Type, TypeVar

from typedload import dataloader, dump
from typedload.exceptions import TypedloadValueError

from .slackclient import SlackClient
from .slackclient.client import LoginInfo


T = TypeVar('T')

USELESS_EVENTS = frozenset((
    'accounts_changed',
    'app_actions_updated',
    'apps_changed',
    'bot_added',
    'bot_changed',
    'channel_archive',  # Weirdly, in that case server sends also a channel_left event
    'channel_marked',
    'channel_sections_channels_removed',
    'channel_sections_channels_upserted',
    'channel_updated',
    'clear_mention_notification',
    'desktop_notification',
    'dnd_updated_user',
    'draft_create',
    'draft_delete',
    'draft_send',
    'draft_sent',
    'draft_update',
    'emoji_changed',
    'file_change',
    'file_created',
    'file_deleted',
    'file_public',
    'file_shared',
    'goodbye',  # Server is disconnecting us
    'group_deleted',
    'group_marked',
    'hello',
    'im_close',
    'im_marked',
    'im_open',
    'mobile_in_app_notification',
    'mpim_marked',
    'pref_change',
    'reaction_added',
    'reaction_removed',
    'subteam_updated',
    'team_join',
    'team_pref_change',
    'thread_marked',
    'thread_subscribed',
    'thread_unsubscribed',
    'unfurl_preview_updated',
    'update_thread_state',
    'view_updated',
))


class ResponseException(Exception):
    pass


class Response(NamedTuple):
    """
    Internally used to parse a response from the API.
    """
    ok: bool
    headers: dict[str, str]
    ts: Optional[float] = None
    error: Optional[str] = None


@dataclass
class File:
    id: str
    url_private: str
    size: int
    user: str
    name: Optional[str] = None
    title: Optional[str] = None
    mimetype: Optional[str] = None


class Topic(NamedTuple):
    """
    In slack, topic is not just a string, but has other fields.
    """
    value: str


class LatestMessage(NamedTuple):
    ts: float

    @property
    def timestamp(self):
        return datetime.datetime.utcfromtimestamp(self.ts)


@dataclass(frozen=True)
class Channel:
    """
    A channel description.

    real_topic tries to use the purpose if the topic is missing
    """
    id: str
    name_normalized: str
    purpose: Topic = None
    topic: Topic = None
    num_members: int = 0
    #: Membership: present on channels, not on groups - but True there.
    is_member: bool = True

    #: Object type. groups have is_group=True, channels is_channel=True
    is_channel: bool = False
    is_group: bool = False
    is_mpim: bool = False
    is_private: bool = False

    latest: Optional[LatestMessage] = None

    @property
    def name(self):
        return self.name_normalized

    @property
    def irc_name(self):
        return '#' + self.name

    @property
    def irc_modes(self):
        modes = '+'
        if self.is_private:
            modes += 'p'
        if self.is_group:
            modes += 'g'
        if self.is_mpim:
            modes += 'i'

        return modes

    @property
    def real_topic(self) -> str:
        if self.topic and self.topic.value:
            return self.topic.value
        elif self.purpose:
            return self.purpose.value
        else:
            return ''


@dataclass(frozen=True)
class MessageThread(Channel):
    thread_ts: str = ''


@dataclass(frozen=True)
class Message:
    channel: str  # The channel id
    user: str  # The user id
    text: str
    subtype: str = None
    thread_ts: Optional[str] = None
    files: list[File] = field(default_factory=list)
    username: str = None

    @property
    def is_action(self):
        return self.subtype == 'me_message'


@dataclass(frozen=True)
class IgnoredMessage:
    """
    We don't care about this message, but as the type is 'message', we need to
    handle it.
    """
    type: Literal['message']
    subtype: Literal['message_replied'] | Literal['channel_name']


class NoChanMessage(NamedTuple):
    text: str
    user: str = None
    thread_ts: Optional[str] = None
    subtype: str = None
    username: str = None


@dataclass
class ChannelCreated:
    type: Literal['channel_created']
    channel: Channel


@dataclass
class ChannelDeleted:
    type: Literal['channel_deleted']
    channel_id: str = field(metadata={'name': 'channel'})
    actor_id: str


@dataclass
class GroupJoined:
    type: Literal['group_joined']
    channel: Channel

    @property
    def channel_id(self):
        return self.channel.id


@dataclass
class ChannelJoined:
    type: Literal['channel_joined']
    channel: Channel

    @property
    def channel_id(self):
        return self.channel.id


@dataclass
class GroupRename:
    type: Literal['group_rename']
    channel: Channel

    @property
    def channel_id(self):
        return self.channel.id


@dataclass
class ChannelRename:
    type: Literal['channel_rename']
    channel: Channel

    @property
    def channel_id(self):
        return self.channel.id


@dataclass
class MPIMJoined:
    type: Literal['mpim_open']
    channel_id: str = field(metadata={'name': 'channel'})
    channel: Channel = None


@dataclass
class GroupLeft:
    type: Literal['group_left']
    channel_id: str = field(metadata={'name': 'channel'})
    actor_id: str = None


@dataclass
class ChannelLeft:
    # https://api.slack.com/events/channel_left
    # "The channel_left event is sometimes sent to all connections for a user
    # when that user leaves a public channel. It is sometimes withheld."
    # lol.
    type: Literal['channel_left']
    channel_id: str = field(metadata={'name': 'channel'})
    actor_id: str = None


@dataclass
class MPIMLeft:
    type: Literal['mpim_close']
    channel_id: str = field(metadata={'name': 'channel'})
    actor_id: str = None


@dataclass
class MessageEdit:
    type: Literal['message']
    subtype: Literal['message_changed']
    channel: str  # The channel id
    previous: NoChanMessage = field(metadata={'name': 'previous_message'})
    current: NoChanMessage = field(metadata={'name': 'message'})
    username: str = None

    @property
    def is_changed(self) -> bool:
        return self.previous.text != self.current.text


@dataclass
class MessageDelete:
    type: Literal['message']
    subtype: Literal['message_deleted']
    channel: str  # The channel id
    previous: NoChanMessage = field(metadata={'name': 'previous_message'}, default=None)
    username: str = None


class UserTyping(NamedTuple):
    type: Literal['user_typing']
    user: str
    channel: str


class Profile(NamedTuple):
    real_name: str = 'noname'
    email: Optional[str] = None
    status_text: str = ''
    is_restricted: bool = False
    is_ultra_restricted: bool = False
    image_original: str = ''
    title: str = ''
    phone: str = ''


@dataclass
class MessageBot:
    type: Literal['message']
    _text: str = field(metadata={'name': 'text'})
    channel: str
    bot_id: str
    _username: str = field(metadata={'name': 'username'}, default=None)
    subtype: str = 'bot_message'  # Literal['bot_message'] = None
    blocks: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    bot_profile: dict[str, Any] = field(default_factory=dict)
    thread_ts: Optional[str] = None
    files: list[File] = field(default_factory=list)

    @property
    def is_action(self):
        return False

    @property
    def username(self):
        username = self._username or self.bot_profile.get('name', 'bot')
        return username.replace(' ', '_')

    @property
    def text(self):
        r = [self._text]
        for block in self.blocks:
            if 'text' in block:
                for line in block['text']['text'].split('\n'):
                    r.append(f"| {line}")
            for element in block.get('elements', ()):
                if element['type'] == 'text':
                    for line in element['text'].split('\n'):
                        r.append(f"| {line}")
        for i in self.attachments:
            t = ""
            if 'text' in i:
                t = i['text']
            elif 'fallback' in i:
                t = i['fallback']
            for line in t.split("\n"):
                r.append("| " + line)
        return '\n'.join(r)


class User(NamedTuple):
    id: str
    name: str
    profile: Profile
    updated: int
    is_owner: bool = False
    is_admin: bool = False
    is_bot: bool = False
    is_app_user: bool = False
    has_2fa: bool = False
    deleted: bool = False

    @property
    def real_name(self) -> str:
        return self.profile.real_name

    @property
    def irc_modes(self) -> str:
        modes = '+'
        if self.is_admin:
            modes += 'a'
        if self.is_owner:
            modes += 'o'
        return modes


@dataclass
class UserChange(NamedTuple):
    type: Literal['user_change']
    user: User


class Presence(NamedTuple):
    presence: str


class IM(NamedTuple):
    id: str
    user: str


class Join(NamedTuple):
    type: Literal['member_joined_channel']
    user: str
    channel: str


class Leave(NamedTuple):
    type: Literal['member_left_channel']
    user: str
    channel: str


@dataclass
class TopicChange:
    type: Literal['message']
    subtype: Literal['channel_topic']
    topic: str
    channel: str
    user: str


@dataclass
class HistoryBotMessage:
    type: Literal['message']
    subtype: Literal['bot_message']
    text: str
    bot_id: Optional[str]
    username: str = 'bot'
    ts: float = 0
    files: list[File] = field(default_factory=list)
    thread_ts: Optional[str] = None
    blocks: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class HistoryMessage:
    type: Literal['message']
    user: str
    text: str
    ts: float
    files: list[File] = field(default_factory=list)
    thread_ts: Optional[str] = None


class NextCursor(NamedTuple):
    next_cursor: str


class History(NamedTuple):
    ok: Literal[True]
    messages: list[HistoryMessage | HistoryBotMessage]
    has_more: bool
    response_metadata: Optional[NextCursor] = None


class Conversations(NamedTuple):
    channels: list[Channel]
    response_metadata: Optional[NextCursor] = None


class Conversation(NamedTuple):
    channel: Channel


SlackEvent = (
    TopicChange |
    IgnoredMessage |
    MessageDelete |
    MessageEdit |
    Message |
    MessageBot |
    Join |
    Leave |
    ChannelCreated |
    ChannelDeleted |
    GroupJoined |
    ChannelJoined |
    MPIMJoined |
    GroupRename |
    ChannelRename |
    GroupLeft |
    ChannelLeft |
    MPIMLeft |
    UserTyping |
    UserChange
)


@dataclass
class SlackStatus:
    """
    Not related to the slack API.
    This is a structure used internally by this module to
    save the status on disk.
    """
    last_timestamp: float = 0.0


class Slack:
    def __init__(self, token: str, cookie: Optional[str], previous_status: Optional[str]) -> None:
        """
        A slack client object.

        token: The slack token
        cookie: If the slack instance also uses a cookie, it must be passed here
        previous_status: Opaque string to restore internal status
                from a different object. Obtained from get_status()
        """
        self.client = SlackClient(token, cookie)
        self._usercache: dict[str, User] = {}
        self._usermapcache: dict[str, User] = {}
        self._usermapcache_keys: list[str]
        self._imcache: dict[str, str] = {}
        self._channelscache: dict[str, Channel] = {}
        self._get_members_cache: dict[str, set[str]] = {}
        self._get_members_cache_cursor: dict[str, Optional[str]] = {}
        self._internalevents: list[SlackEvent] = []
        self._sent_by_self: set[float] = set()
        self._wsblock: int = 0  # Semaphore to block the socket and avoid events being received before their API call ended.
        self.login_info: Optional[LoginInfo] = None
        self.loader = dataloader.Loader()

        if previous_status is None:
            self._status = SlackStatus()
        else:
            self._status = self.tload(json.loads(previous_status), SlackStatus)

    def close(self):
        self.client.close()

    def tload(self, data: Any, type_: Type[T]) -> T:
        try:
            return self.loader.load(data, type_)
        except TypedloadValueError:
            logging.error('Unable to parse', exc_info=True)
            logging.error(data)
            raise

    async def login(self) -> None:
        """
        Set the login_info field
        """
        logging.info('Login in slack')
        self.login_info = await self.client.login(15)

    async def get_history(
        self,
        channel: Channel | IM | str,
        ts: str,
        cursor: Optional[NextCursor] = None,
        limit: int = 1000,
        inclusive: bool = False
    ) -> History:
        p = await self.client.api_call(
            'conversations.history',
            channel=channel if isinstance(channel, str) else channel.id,
            oldest=ts,
            limit=limit,
            cursor=cursor.next_cursor if cursor else None,
            inclusive=inclusive,
        )
        return self.tload(p, History)

    async def count_regular_users(self):
        return len([u for u in self._usercache.values() if not u.is_bot])

    async def count_bots(self):
        return len([u for u in self._usercache.values() if u.is_bot])

    async def count_admins(self):
        return len([u for u in self._usercache.values() if u.is_admin])

    async def get_thread_history(self, channel: str, thread_id: str) -> list[HistoryMessage | HistoryBotMessage]:
        r: list[HistoryMessage | HistoryBotMessage] = []
        cursor = None
        logging.info('Thread history %s %s', channel, thread_id)
        while True:
            logging.info('Cursor')
            p = await self.client.api_call(
                'conversations.replies',
                channel=channel,
                ts=thread_id,
                limit=1000,
                cursor=cursor,
            )
            response = self.tload(p, Response)
            if not response.ok:
                logging.debug(f'Unable to find thread {thread_id}: {response.error}')
                return []

            try:
                response = self.tload(p, History)
            except TypedloadValueError:
                break

            r += [i for i in response.messages if i.ts != i.thread_ts]
            if response.has_more and response.response_metadata:
                cursor = response.response_metadata.next_cursor
            else:
                break
        logging.info('Thread fetched')
        if len(r) > 0:
            # if not, maybe a thread with all messages deleted, handle it anyway.
            r[0].thread_ts = None
        return r

    async def _history(self) -> None:
        '''
        Obtain the history from the last known event and
        inject fake events as if the messages are coming now.
        '''
        logging.info('Fetching history...')

        if self._status.last_timestamp == 0:
            logging.info('No last known timestamp. Unable to fetch history')
            return

        last_timestamp = self._status.last_timestamp
        FOUR_DAYS = 60 * 60 * 24 * 4
        if time() - last_timestamp > FOUR_DAYS:
            logging.info('Last timestamp is too old. Defaulting to 4 days.')
            last_timestamp = time() - FOUR_DAYS
        dt = datetime.datetime.fromtimestamp(last_timestamp)
        logging.info('Last known timestamp %s', dt)

        chats: Sequence[IM | Channel] = []
        chats += list((await self.channels()).values()) + await self.get_ims()  # type: ignore
        for channel in chats:
            if isinstance(channel, Channel):
                if not channel.is_member:
                    continue

                logging.info('Downloading logs from channel %s', channel.name_normalized)
            else:
                logging.info('Downloading logs from IM %s', channel.user)

            cursor = None
            while True:  # Loop to iterate the cursor
                logging.info('Calling cursor')
                try:
                    response = await self.get_history(channel, str(last_timestamp))
                except TypedloadValueError:
                    break
                msg_list = list(response.messages)
                while msg_list:
                    msg = msg_list.pop(0)

                    # The last seen message is sent again, skip it
                    if msg.ts == last_timestamp:
                        continue
                    # Update the last seen timestamp
                    if self._status.last_timestamp < msg.ts:
                        self._status.last_timestamp = msg.ts

                    # History for the thread
                    if msg.thread_ts and float(msg.thread_ts) == msg.ts:
                        history = await self.get_thread_history(channel.id, msg.thread_ts)
                        history.reverse()
                        msg_list = history + msg_list
                        continue

                    # Inject the events
                    if isinstance(msg, HistoryMessage):
                        self._internalevents.append(Message(
                            channel=channel.id,
                            text=msg.text,
                            user=msg.user,
                            thread_ts=msg.thread_ts,
                            files=msg.files,
                        ))
                    elif isinstance(msg, HistoryBotMessage):
                        self._internalevents.append(MessageBot(
                            type='message',
                            subtype='bot_message',
                            _text=msg.text,
                            attachments=msg.attachments,
                            blocks=msg.blocks,
                            _username=msg.username,
                            channel=channel.id,
                            bot_id=msg.bot_id,
                            thread_ts=msg.thread_ts,
                        ))

                if response.has_more and response.response_metadata:
                    next_cursor = response.response_metadata.next_cursor
                    if next_cursor == cursor:
                        break
                    cursor = next_cursor
                else:
                    break

    def get_status(self) -> str:
        '''
        A status string that will be passed back when this is started again
        '''
        return json.dumps(dump(self._status), ensure_ascii=True)

    async def is_user_away(self, user: User | str) -> bool:
        if isinstance(user, User):
            user_id = user.id
        else:
            user_id = user

        r = await self.client.api_call('users.getPresence', user=user_id)
        response = self.tload(r, Response)
        if not response.ok:
            raise ResponseException(response.error)

        presence = self.tload(r, Presence)
        return presence.presence == 'away'

    async def away(self, is_away: bool) -> None:
        """
        Forces the aways status or lets slack decide
        """
        status = 'away' if is_away else 'auto'
        r = await self.client.api_call('users.setPresence', presence=status)
        response = self.tload(r, Response)
        if not response.ok:
            raise ResponseException(response.error)

    async def typing(self, channel: Channel | str) -> None:
        """
        Sends a typing event to slack
        """
        if isinstance(channel, Channel):
            ch_id = channel.id
        else:
            ch_id = channel
        await self.client.wspacket(type='typing', channel=ch_id)

    async def topic(self, channel: Channel, topic: str) -> None:
        r = await self.client.api_call('conversations.setTopic', channel=channel.id, topic=topic)
        response: Response = self.tload(r, Response)
        if not response.ok:
            raise ResponseException(response.error)

    async def kick(self, channel: Channel, user: User) -> None:
        r = await self.client.api_call('conversations.kick', channel=channel.id, user=user.id)
        response = self.tload(r, Response)
        if not response.ok:
            raise ResponseException(response.error)

    async def join(self, channel: Channel) -> None:
        r = await self.client.api_call('conversations.join', channel=channel.id)
        response = self.tload(r, Response)
        if not response.ok:
            raise ResponseException(response.error)

    async def invite(self, channel: Channel, user: User | list[User]) -> None:
        if isinstance(user, User):
            ids = user.id
        else:
            if len(user) > 30:
                raise ValueError('No more than 30 users allowed')
            ids = ','.join(i.id for i in user)

        r = await self.client.api_call('conversations.invite', channel=channel.id, users=ids)
        response = self.tload(r, Response)
        if not response.ok:
            raise ResponseException(response.error)

    async def get_members(self, channel: str | Channel, refresh: bool = None) -> set[str]:
        """
        Returns the list (as a set) of users in a channel.

        It performs caching. Every time the function is called, a new batch is
        requested, until all the users are cached, and then no new requests
        are performed, and the same data is returned.

        When events happen, the cache needs to be updated or cleared.

        If refresh is True, force the cache to be updated
        If refresh is False, the cache is never updated
        """
        if isinstance(channel, Channel):
            id_ = channel.id
        else:
            id_ = channel

        cached = self._get_members_cache.get(id_, set())
        cursor = self._get_members_cache_cursor.get(id_)
        if (cursor == '' and refresh is not None) or refresh is False:
            # The cursor is fully iterated
            return cached
        kwargs = {}
        if cursor:
            kwargs['cursor'] = cursor
        r = await self.client.api_call('conversations.members', channel=id_, limit=5000, **kwargs)  # type: ignore
        response = self.tload(r, Response)
        if not response.ok:
            raise ResponseException(response.error)

        newusers = self.tload(r['members'], set[str])

        # Generate all the Join events, if this is not the 1st iteration
        if id_ in self._get_members_cache:
            for i in newusers.difference(cached):
                self._internalevents.append(Join('member_joined_channel', user=i, channel=id_))

        self._get_members_cache[id_] = cached.union(newusers)
        self._get_members_cache_cursor[id_] = r.get('response_metadata', {}).get('next_cursor')
        return self._get_members_cache[id_]

    async def channels(self, refresh: bool = None) -> dict[str, Channel]:
        """
        Returns the list of slack channels

        if refresh is True, the local cache is cleared
        if refresh is False, the cache is not refreshed, even if empty
        """
        if refresh is True:
            self._channelscache.clear()

        if self._channelscache or refresh is False:
            return self._channelscache

        cursor = None
        while True:
            r = await self.client.api_call(
                'conversations.list',
                cursor=cursor,
                exclude_archived=True,
                types='public_channel,private_channel,mpim',
                limit=1000,  # In vain hope that slack would not ignore this
            )
            response = self.tload(r, Response)

            if response.ok:
                conv = self.tload(r, Conversations)
                for chan in conv.channels:
                    self._channelscache[chan.id] = chan
                # For this API, slack sends an empty string as next cursor, just to show off their programming "skillz"
                if not conv.response_metadata or not conv.response_metadata.next_cursor:
                    break
                cursor = conv.response_metadata.next_cursor
            else:
                raise ResponseException(response.error)
        return self._channelscache

    async def get_channel(self, id_: str, refresh: bool = False) -> Channel:
        """
        Returns a channel object from a slack channel id

        raises KeyError if it doesn't exist.
        """
        if refresh or id_ not in self._channelscache:
            await self.channels(refresh=True)

        return self._channelscache[id_]

    async def get_channel_by_name(self, name: str) -> Channel:
        """
        Returns a channel object from a slack channel id

        raises KeyError if it doesn't exist.
        """
        for i in range(2):
            for c in (await self.channels(refresh=bool(i))).values():
                if c.name == name:
                    return c
        raise KeyError()

    async def get_thread(self, thread_ts: str, original_channel: str, source: str) -> MessageThread:
        """
        Creates a fake channel class for a chat thread
        """
        try:
            channel = (await self.get_channel(original_channel)).name_normalized
        except KeyError:
            channel = source

        # Get head message
        history = await self.get_history(original_channel, thread_ts, None, 1, True)

        try:
            msg = history.messages.pop()
        except IndexError:
            t = Topic(f'{channel}: Deleted thread')
        else:
            user = (await self.get_user(msg.user)).name if isinstance(msg, HistoryMessage) else 'bot'

            # Top message is a file
            if msg.text == '' and msg.files:
                f = msg.files[0]
                original_txt = f'{f.title} {f.mimetype} {f.url_private}'
            else:
                original_txt = msg.text.strip().replace('\n', ' | ')

            t = Topic(f'{user} in {channel}: {original_txt}')

        return MessageThread(
            id=original_channel,
            name_normalized=f't-{channel}-{thread_ts}',
            purpose=t,
            topic=t,
            thread_ts=thread_ts,
        )

    async def get_im(self, im_id: str) -> Optional[IM]:
        if not im_id.startswith('D'):
            return None
        for uid, imid in self._imcache.items():
            if im_id == imid:
                return IM(user=uid, id=imid)

        for im in await self.get_ims():
            self._imcache[im.user] = im.id
            if im.id == im_id:
                return im
        return None

    async def get_ims(self) -> list[IM]:
        """
        Returns a list of the IMs

        Some bullshit slack invented because 1 to 1 conversations
        need to have an ID to send to, you can't send directly to
        a user.
        """
        r = await self.client.api_call(
            "conversations.list",
            exclude_archived=True,
            types='im', limit=1000
        )
        response = self.tload(r, Response)
        if response.ok:
            return self.tload(r['channels'], list[IM])
        raise ResponseException(response.error)

    async def get_user_by_name(self, name: str) -> User:
        if name not in self._usermapcache:
            await self.prefetch_users()

        return self._usermapcache[name]

    async def prefetch_users(self) -> None:
        """
        Prefetch all team members for the slack team.
        """
        r = await self.client.api_call("users.list")
        response = self.tload(r, Response)
        if response.ok:
            for user in self.tload(r['members'], list[User]):
                self._usercache[user.id] = user
                self._usermapcache[user.name] = user
            self._usermapcache_keys = []

    async def get_user(self, id_: str) -> User:
        """
        Returns a user object from a slack user id

        raises KeyError if it does not exist
        """
        if id_ in self._usercache:
            return self._usercache[id_]

        r = await self.client.api_call("users.info", user=id_)
        response = self.tload(r, Response)
        if response.ok:
            u = self.tload(r['user'], User)
            self._usercache[id_] = u
            if u.name not in self._usermapcache:
                self._usermapcache_keys = []
            self._usermapcache[u.name] = u
            return u

        raise KeyError(response)

    async def send_file(self, channel_id: str, filename: str, thread_ts: Optional[str]) -> None:
        """
        Send a file to a channel or group or whatever
        """
        with open(filename, 'rb') as f:
            r = await self.client.api_call(
                'files.upload',
                channels=channel_id,
                thread_ts=thread_ts,
                file=f,
            )
        response = self.tload(r, Response)
        if response.ok:
            return
        raise ResponseException(response.error)

    def _triage_sent_by_self(self) -> None:
        """
        Clear all the old leftovers in
        _sent_by_self
        """
        r = []
        for i in self._sent_by_self:
            if time() - i >= 10:
                r.append(i)
        for i in r:
            self._sent_by_self.remove(i)

    async def send_message(self, channel: Channel | MessageThread, msg: str, action: bool) -> None:
        thread_ts = channel.thread_ts if isinstance(channel, MessageThread) else None
        return await self._send_message(channel.id, msg, action, thread_ts)

    async def _send_message(self, channel_id: str, msg: str, action: bool, thread_ts: Optional[str]) -> None:
        """
        Send a message to a channel or group or whatever
        """
        if action:
            api = 'chat.meMessage'
        else:
            api = 'chat.postMessage'

        try:
            kwargs = {}

            if thread_ts:
                kwargs['thread_ts'] = thread_ts

            self._wsblock += 1
            r = await self.client.api_call(
                api,
                channel=channel_id,
                text=msg,
                as_user=True,
                **kwargs,  # type: ignore
            )
            response = self.tload(r, Response)
            if response.ok and response.ts:
                # Mark this channel as read
                await self.client.api_call(
                    'conversations.mark',
                    channel=channel_id,
                    ts=response.ts
                )

                self._sent_by_self.add(response.ts)
                return
            raise ResponseException(response.error)
        finally:
            self._wsblock -= 1

    async def send_message_to_user(self, user: User, msg: str, action: bool):
        """
        Send a message to a user, pass the user id
        """

        # 1 to 1 chats are like channels, but use a dedicated API,
        # so to deliver a message to them, a channel id is required.
        # Those are called IM.

        if user.id in self._imcache:
            # channel id is cached
            channel_id = self._imcache[user.id]
        else:
            # Find the channel id
            found = False
            # Iterate over all the existing conversations
            for i in await self.get_ims():
                if i.user == user.id:
                    channel_id = i.id
                    found = True
                    break
            # A conversation does not exist, create one
            if not found:
                r = await self.client.api_call(
                    "im.open",
                    return_im=True,
                    user=user.id,
                )
                response = self.tload(r, Response)
                if not response.ok:
                    raise ResponseException(response.error)
                channel_id = r['channel']['id']

            self._imcache[user.id] = channel_id

        await self._send_message(channel_id, msg, action, None)

    async def events(self) -> Optional[SlackEvent]:
        """
        This returns the events from the slack websocket
        """
        if self._internalevents:
            yield self._internalevents.pop()
            return

        try:
            events = await self.client.rtm_read()
        except Exception:
            events = []
            logging.info('Connecting to slack...')
            self.login_info = await self.client.rtm_connect(5)
            await self._history()
            logging.info('Connected to slack')
            return

        while self._wsblock:  # Retry until the semaphore is free
            await asyncio.sleep(0.01)

        for event in events:
            t = event.get('type')
            ts = float(event.get('ts', 0))

            if ts > self._status.last_timestamp:
                self._status.last_timestamp = ts

            if ts in self._sent_by_self:
                self._sent_by_self.remove(ts)
                continue

            if t in USELESS_EVENTS:
                continue

            logging.debug(event)
            try:
                ev: Optional[SlackEvent] = self.tload(
                    event,
                    SlackEvent  # type: ignore
                )
            except TypedloadValueError:
                continue

            logging.debug(ev)

            self._triage_sent_by_self()

            if isinstance(ev, (Join, Leave)) and ev.channel in self._get_members_cache:
                if isinstance(ev, Join):
                    self._get_members_cache[ev.channel].add(ev.user)
                else:
                    self._get_members_cache[ev.channel].discard(ev.user)

            if isinstance(ev, UserChange):
                if ev.user.id in self._usercache:
                    del self._usercache[ev.user.id]
                    # FIXME don't know if it is wise, maybe it gets lost forever del self._usermapcache[u.name]
                    # TODO make an event for this
                else:
                    logging.info(event)

            yield ev
