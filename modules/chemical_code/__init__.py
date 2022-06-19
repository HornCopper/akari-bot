import asyncio
import random
import re
import traceback

from bs4 import BeautifulSoup

from tenacity import retry, stop_after_attempt

from datetime import datetime

from core.component import on_command
from core.elements import MessageSession, Image, Plain
from core.utils import get_url, download_to_cache, random_cache_path
from core.logger import Logger

from PIL import Image as PILImage


csr_link = 'https://www.chemspider.com'  # ChemSpider 的链接


@retry(stop=stop_after_attempt(3), reraise=True)
async def search_csr(id=None):  # 根据 ChemSpider 的 ID 查询 ChemSpider 的链接，留空（将会使用缺省值 None）则随机查询
    if id is not None:  # 如果传入了 ID，则使用 ID 查询
        answer = id
    else:
        answer = random.randint(1, 12198914)  # 否则随机查询一个题目
    get = await get_url(csr_link + '/Search.aspx?q=' + str(answer), 200, fmt='text')  # 在 ChemSpider 上搜索此化学式或 ID
    # Logger.info(get)
    soup = BeautifulSoup(get, 'html.parser')  # 解析 HTML
    name = soup.find('span',
                     id='ctl00_ctl00_ContentSection_ContentPlaceHolder1_RecordViewDetails_rptDetailsView_ctl00_prop_MF').text  # 获取化学式名称
    values_ = re.split(r'[A-Za-z]+', name)  # 去除化学式名称中的字母
    value = 0  # 起始元素记数，忽略单个元素（有无意义不大）
    for v in values_:  # 遍历剔除字母后的数字
        if v.isdigit():
            value += int(v)  # 加一起
    wh = 500 * value // 100
    if wh < 500:
        wh = 500
    return {'name': name, 'image': f'https://www.chemspider.com/ImagesHandler.ashx?id={answer}&w={wh}&h={wh}'}


cc = on_command('chemical_code', alias=['cc', 'chemicalcode'], desc='化学式验证码测试', developers=['OasisAkari'])
play_state = {}  # 创建一个空字典用于存放游戏状态


@cc.handle()  # 直接使用 cc 命令将触发此装饰器
async def chemical_code_by_random(msg: MessageSession):
    await chemical_code(msg)  # 将消息会话传入 chemical_code 函数


@cc.handle('stop {停止当前的游戏。}')
async def s(msg: MessageSession):
    state = play_state.get(msg.target.targetId, False)  # 尝试获取 play_state 中是否有此对象的游戏状态
    if state:  # 若有
        if state['active']:  # 检查是否为活跃状态
            play_state[msg.target.targetId]['active'] = False  # 标记为非活跃状态
            await msg.sendMessage(f'已停止，正确答案是 {state["answer"]}', quote=False)  # 发送存储于 play_state 中的答案
        else:
            await msg.sendMessage('当前无活跃状态的游戏。')
    else:
        await msg.sendMessage('当前无活跃状态的游戏。')


@cc.handle('<csid> {根据 ChemSpider ID 出题}')
async def chemical_code_by_id(msg: MessageSession):
    id = msg.parsed_msg['<csid>']  # 从已解析的消息中获取 ChemSpider ID
    if id.isdigit():  # 如果 ID 为纯数字
        await chemical_code(msg, id)  # 将消息会话和 ID 一并传入 chemical_code 函数
    else:
        await msg.finish('请输入纯数字ID！')


async def chemical_code(msg: MessageSession, id=None):  # 要求传入消息会话和 ChemSpider ID，ID 留空将会使用缺省值 None
    if msg.target.targetId in play_state and play_state[msg.target.targetId]['active']:  # 检查对象（群组或私聊）是否在 play_state 中有记录及是否为活跃状态
        await msg.finish('当前有一局游戏正在进行中。')
    play_state.update({msg.target.targetId: {'active': True}})  # 若无，则创建一个新的记录并标记为活跃状态
    try:
        csr = await search_csr(id)  # 尝试获取 ChemSpider ID 对应的化学式列表
    except Exception as e:  # 意外情况
        traceback.print_exc()  # 打印错误信息
        play_state[msg.target.targetId]['active'] = False  # 将对象标记为非活跃状态
        return await msg.finish('发生错误：拉取题目失败，请重新发起游戏。')
    # print(csr)
    play_state[msg.target.targetId]['answer'] = csr['name']  # 将正确答案标记于 play_state 中存储的对象中
    Logger.info(f'Answer: {csr["name"]}')  # 在日志中输出正确答案
    Logger.info(f'Image: {csr["image"]}')  # 在日志中输出图片链接
    download = await download_to_cache(csr['image'])  # 从结果中获取链接并下载图片

    with PILImage.open(download) as im:  # 打开下载的图片
        datas = im.getdata()  # 获取图片数组
        newData = []
        for item in datas:  # 对每个像素点进行处理
            if item[3] == 0:  # 如果为透明
                newData.append((230, 230, 230))  # 设置白底
            else:
                newData.append(tuple(item[:3]))  # 否则保留原图像素点
        image = PILImage.new("RGBA", im.size)  # 创建新图片
        image.getdata()  # 获取新图片数组
        image.putdata(newData)  # 将处理后的数组覆盖新图片
        newpath = random_cache_path() + '.png'  # 创建新文件名
        image.save(newpath)  # 保存新图片

    await msg.sendMessage([Image(newpath),
                           Plain('请于2分钟内发送正确答案。（请使用字母表顺序，如：CHBrClF）')])
    time_start = datetime.now().timestamp()  # 记录开始时间

    async def ans(msg: MessageSession, answer):  # 定义回答函数的功能
        wait = await msg.waitAnyone()  # 等待对象内的任意人回答
        if play_state[msg.target.targetId]['active']:  # 检查对象是否为活跃状态
            if wait.asDisplay() != answer:  # 如果回答不正确
                Logger.info(f'{wait.asDisplay()} != {answer}')  # 输出日志
                return await ans(wait, answer)  # 进行下一轮检查
            else:
                await wait.sendMessage('回答正确。')
                play_state[msg.target.targetId]['active'] = False  # 将对象标记为非活跃状态

    async def timer(start):  # 计时器函数
        if play_state[msg.target.targetId]['active']:  # 检查对象是否为活跃状态
            if datetime.now().timestamp() - start > 120:  # 如果超过2分钟
                await msg.sendMessage(f'已超时，正确答案是 {play_state[msg.target.targetId]["answer"]}', quote=False)
                play_state[msg.target.targetId]['active'] = False
            else:  # 如果未超时
                await asyncio.sleep(1)  # 等待1秒
                await timer(start)  # 重新调用计时器函数

    await asyncio.gather(ans(msg, csr['name']), timer(time_start))  # 同时启动回答函数和计时器函数

