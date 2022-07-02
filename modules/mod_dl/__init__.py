from core.component import on_command
from core.elements import Url
from core.builtins.message import MessageSession

from .mod_dl import curseforge as d

mod_dl = on_command(
    bind_prefix='mod_dl',
    desc='下载CurseForge上的Mod。',
    developers=['HornCopper'],
    recommend_modules=['mcmod'],
    alias='moddl')


@mod_dl.handle('<mod_name> <mcversion> {通过模组名获取模组下载链接，CloudFlare CDN支持。}')
async def main(msg: MessageSession):
    info = await d(msg.parsed_msg['<mod_name>'], msg.parsed_msg['<mcversion>'])
    if not info['success']:
        await msg.finish(info['msg'])
    link = info["download_link"]
    name = info["filename"]
    status = info["status"]
    message = f'下载链接：{str(Url(link))}\n文件名：{name}\n版本状态：{status}'
    await msg.finish(message)
