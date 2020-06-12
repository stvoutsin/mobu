"""Handlers for controlling users, ``/<app-name>/user/``."""

__all__ = [
    "post_user",
    "get_users",
    "get_user",
    "delete_user",
]

from aiohttp import web

from sciencemonkey.handlers import routes
from sciencemonkey.monkeybusinessfactory import MonkeyBusinessFactory


@routes.post("/user")
async def post_user(request: web.Request) -> web.Response:
    """POST /user

    Create a user to use for load testing.  This takes all the
    required information to create a user, including username,
    uid, and other related fields that will be used in the ticket.
    """
    body = await request.json()
    logger = request["safir/logger"]
    logger.info(body)
    manager = request.config_dict["sciencemonkey/monkeybusinessmanager"]
    monkey = MonkeyBusinessFactory.create(body)
    await manager.manage_monkey(monkey)
    data = {"user": monkey.user.username}
    return web.json_response(data)


@routes.get("/user")
async def get_users(request: web.Request) -> web.Response:
    """GET /user

    Get a list of all the users currently used for load testing.
    """
    data = []
    manager = request.config_dict["sciencemonkey/monkeybusinessmanager"]
    for username, monkey in manager.monkeys:
        data.append(username)
    return web.json_response(data)


@routes.get("/user/{name}")
async def get_user(request: web.Request) -> web.Response:
    """GET /user/{name}

    Get info on a particular user.
    """
    username = request.match_info["name"]
    manager = request.config_dict["sciencemonkey/monkeybusinessmanager"]
    if username not in manager.monkeys:
        raise web.HTTPNotFound()
    monkey = manager.monkeys[username]
    data = {"user": username, "business": str(monkey)}
    return web.json_response(data)


@routes.delete("/user/{name}")
async def delete_user(request: web.Request) -> web.Response:
    """DELETE /user/{name}

    Delete a particular user, which will cancel all testing it is doing.
    """
    username = request.match_info["name"]
    manager = request.config_dict["sciencemonkey/monkeybusinessmanager"]
    manager.release_monkey(username)
    return web.HTTPOk()
