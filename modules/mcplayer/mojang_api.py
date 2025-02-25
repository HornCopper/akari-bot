import ujson as json
from PIL import Image

from core.utils import get_url, download_to_cache


async def uuid_to_name(uuid):
    res = json.loads(await get_url(f'https://api.mojang.com/user/profiles/{uuid}/names'))
    return res[0]['name']


async def name_to_uuid(name):
    res = json.loads(await get_url(f'https://api.mojang.com/users/profiles/minecraft/{name}'))
    return res['id']


async def uuid_to_skin_and_cape(uuid):
    skin = await download_to_cache(
        'https://crafatar.com/renders/body/' + uuid + '?overlay')
    is_cape = True
    try:
        await get_url('https://crafatar.com/capes/' + uuid, status_code=200)
    except ValueError:
        is_cape = False
    path = None
    if is_cape:
        cape = Image.open(await download_to_cache(
            'https://crafatar.com/capes/' + uuid))
        cape.crop((0, 0, 10, 16))
        path = 'cache/' + uuid + '_fixed.png'
        cape.save(path)
    return {'skin': skin, 'cape': path}


__all__ = ['uuid_to_name', 'name_to_uuid', 'uuid_to_skin_and_cape']
