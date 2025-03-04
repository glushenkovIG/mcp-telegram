from __future__ import annotations

import logging
import sys
import typing as t
from functools import singledispatch

from mcp.types import (
    EmbeddedResource,
    ImageContent,
    TextContent,
    Tool,
)
from pydantic import BaseModel, ConfigDict
from telethon import TelegramClient, custom, functions, types  # type: ignore[import-untyped]

from .telegram import create_client

logger = logging.getLogger(__name__)


# How to add a new tool:
#
# 1. Create a new class that inherits from ToolArgs
#    ```python
#    class NewTool(ToolArgs):
#        """Description of the new tool."""
#        pass
#    ```
#    Attributes of the class will be used as arguments for the tool.
#    The class docstring will be used as the tool description.
#
# 2. Implement the tool_runner function for the new class
#    ```python
#    @tool_runner.register
#    async def new_tool(args: NewTool) -> t.Sequence[TextContent | ImageContent | EmbeddedResource]:
#        pass
#    ```
#    The function should return a sequence of TextContent, ImageContent or EmbeddedResource.
#    The function should be async and accept a single argument of the new class.
#
# 3. Done! Restart the client and the new tool should be available.


class ToolArgs(BaseModel):
    model_config = ConfigDict()


@singledispatch
async def tool_runner(
    args,  # noqa: ANN001
) -> t.Sequence[TextContent | ImageContent | EmbeddedResource]:
    raise NotImplementedError(f"Unsupported type: {type(args)}")


def tool_description(args: type[ToolArgs]) -> Tool:
    return Tool(
        name=args.__name__,
        description=args.__doc__,
        inputSchema=args.model_json_schema(),
    )


def tool_args(tool: Tool, *args, **kwargs) -> ToolArgs:  # noqa: ANN002, ANN003
    return sys.modules[__name__].__dict__[tool.name](*args, **kwargs)


### ListDialogs ###


class ListDialogs(ToolArgs):
    """List available dialogs, chats and channels."""

    unread: bool = False
    archived: bool = False
    ignore_pinned: bool = False


@tool_runner.register
async def list_dialogs(
    args: ListDialogs,
) -> t.Sequence[TextContent | ImageContent | EmbeddedResource]:
    client: TelegramClient
    logger.info("method[ListDialogs] args[%s]", args)

    response: list[TextContent] = []
    async with create_client() as client:
        dialog: custom.dialog.Dialog
        async for dialog in client.iter_dialogs(archived=args.archived, ignore_pinned=args.ignore_pinned):
            if args.unread and dialog.unread_count == 0:
                continue
            msg = (
                f"name='{dialog.name}' id={dialog.id} "
                f"unread={dialog.unread_count} mentions={dialog.unread_mentions_count}"
            )
            response.append(TextContent(type="text", text=msg))

    return response


### ListMessages ###


class ListMessages(ToolArgs):
    """
    List messages in a given dialog, chat or channel. The messages are listed in order from newest to oldest.

    If `unread` is set to `True`, only unread messages will be listed. Once a message is read, it will not be
    listed again.

    If `limit` is set, only the last `limit` messages will be listed. If `unread` is set, the limit will be
    the minimum between the unread messages and the limit.
    """

    dialog_id: int
    unread: bool = False
    limit: int = 100


@tool_runner.register
async def list_messages(
    args: ListMessages,
) -> t.Sequence[TextContent | ImageContent | EmbeddedResource]:
    client: TelegramClient
    logger.info("method[ListMessages] args[%s]", args)

    response: list[TextContent] = []
    async with create_client() as client:
        result = await client(functions.messages.GetPeerDialogsRequest(peers=[args.dialog_id]))
        if not result:
            raise ValueError(f"Channel not found: {args.dialog_id}")

        if not isinstance(result, types.messages.PeerDialogs):
            raise TypeError(f"Unexpected result: {type(result)}")

        for dialog in result.dialogs:
            logger.debug("dialog: %s", dialog)
        for message in result.messages:
            logger.debug("message: %s", message)

        iter_messages_args: dict[str, t.Any] = {
            "entity": args.dialog_id,
            "reverse": False,
        }
        if args.unread:
            iter_messages_args["limit"] = min(dialog.unread_count, args.limit)
        else:
            iter_messages_args["limit"] = args.limit

        logger.debug("iter_messages_args: %s", iter_messages_args)
        async for message in client.iter_messages(**iter_messages_args):
            logger.debug("message: %s", type(message))
            if isinstance(message, custom.Message) and message.text:
                logger.debug("message: %s", message.text)
                response.append(TextContent(type="text", text=message.text))

    return response


class SearchHashtags(ToolArgs):
    """Search for specific hashtags in a Telegram group and export results to CSV."""
    group_id: int
    hashtags: list[str]
    output_format: str = "csv"  # csv or txt
    output_file: str = "hashtag_results"
    api_id: int
    api_hash: str

    class Config:
        json_schema_extra = {
            "example": {
                "group_id": -1001234567890,
                "hashtags": ["#intro", "#интро", "#iam", "#whois"],
                "output_format": "csv",
                "output_file": "intro_messages",
                "api_id": 26162406,
                "api_hash": "7a005c82feee57d782a7e2f8399ddaf6"
            }
        }

@tool_runner.register
async def search_hashtags(args: SearchHashtags) -> t.Sequence[TextContent]:
    """Search for specific hashtags in a Telegram group and export results."""
    try:
        # Initialize Telegram client
        client = await create_client()
        
        # Get the group entity
        group = await client.get_entity(args.group_id)
        
        # Initialize results
        messages = []
        total_count = 0
        
        # Search for messages with hashtags
        async for message in client.iter_messages(group):
            if message.text:
                for hashtag in args.hashtags:
                    if hashtag.lower() in message.text.lower():
                        messages.append({
                            'date': message.date.isoformat(),
                            'user_id': message.sender_id,
                            'username': message.sender.username if message.sender else 'Unknown',
                            'message': message.text,
                            'hashtag': hashtag
                        })
                        total_count += 1
                        break  # Break once we find a matching hashtag
        
        # Export results
        if args.output_format == "csv":
            import csv
            with open(f"{args.output_file}.csv", 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=['date', 'user_id', 'username', 'message', 'hashtag'])
                writer.writeheader()
                writer.writerows(messages)
        else:  # txt format
            with open(f"{args.output_file}.txt", 'w', encoding='utf-8') as f:
                for msg in messages:
                    f.write(f"Date: {msg['date']}\n")
                    f.write(f"User: {msg['username']} (ID: {msg['user_id']})\n")
                    f.write(f"Message: {msg['message']}\n")
                    f.write(f"Hashtag: {msg['hashtag']}\n")
                    f.write("-" * 50 + "\n")
        
        await client.disconnect()
        
        return [
            TextContent(
                text=f"Successfully exported {total_count} messages with hashtags {', '.join(args.hashtags)} to {args.output_file}.{args.output_format}"
            )
        ]
        
    except Exception as e:
        return [
            TextContent(
                text=f"Error searching hashtags: {str(e)}"
            )
        ]
