import re
import traceback
from datetime import datetime

from aiocqhttp.exceptions import ActionFailed

from config import Config
from core.builtins.message import MessageSession
from core.elements import Command, command_prefix, ExecutionLockList, RegexCommand, ErrorMessage
from core.exceptions import AbuseWarning, FinishedException, InvalidCommandFormatError, InvalidHelpDocTypeError, \
    WaitCancelException
from core.loader import ModulesManager
from core.logger import Logger
from core.parser.command import CommandParser
from core.tos import warn_target
from core.utils import removeIneffectiveText, removeDuplicateSpace
from database import BotDBUtil


enable_tos = Config('enable_tos')

counter_same = {}  # 命令使用次数计数（重复使用单一命令）
counter_all = {}  # 命令使用次数计数（使用所有命令）

temp_ban_counter = {}  # 临时封禁计数


async def remove_temp_ban(msg: MessageSession):
    is_temp_banned = temp_ban_counter.get(msg.target.senderId)
    if is_temp_banned is not None:
        del temp_ban_counter[msg.target.senderId]


async def msg_counter(msg: MessageSession, command: str):
    same = counter_same.get(msg.target.senderId)
    if same is None or datetime.now().timestamp() - same['ts'] > 300 or same[
        'command'] != command:  # 检查是否滥用（重复使用同一命令）
        counter_same[msg.target.senderId] = {'command': command, 'count': 1,
                                             'ts': datetime.now().timestamp()}
    else:
        same['count'] += 1
        if same['count'] > 10:
            raise AbuseWarning('一段时间内使用相同命令的次数过多')
    all_ = counter_all.get(msg.target.senderId)
    if all_ is None or datetime.now().timestamp() - all_['ts'] > 300:  # 检查是否滥用（重复使用同一命令）
        counter_all[msg.target.senderId] = {'count': 1,
                                            'ts': datetime.now().timestamp()}
    else:
        all_['count'] += 1
        if all_['count'] > 20:
            raise AbuseWarning('一段时间内使用命令的次数过多')


async def temp_ban_check(msg: MessageSession):
    is_temp_banned = temp_ban_counter.get(msg.target.senderId)
    if is_temp_banned is not None:
        ban_time = datetime.now().timestamp() - is_temp_banned['ts']
        if ban_time < 300:
            if is_temp_banned['count'] < 2:
                is_temp_banned['count'] += 1
                return await msg.sendMessage('提示：\n'
                                             '由于你的行为触发了警告，我们已对你进行临时封禁。\n'
                                             f'距离解封时间还有{str(int(300 - ban_time))}秒。')
            elif is_temp_banned['count'] <= 5:
                is_temp_banned['count'] += 1
                return await msg.sendMessage('即使是触发了临时封禁，继续使用命令还是可能会导致你被再次警告。\n'
                                             f'距离解封时间还有{str(int(300 - ban_time))}秒。')
            else:
                raise AbuseWarning('无视临时封禁警告')


async def parser(msg: MessageSession, require_enable_modules: bool = True, prefix: list = None,
                 running_mention: bool = False):
    """
    接收消息必经的预处理器
    :param msg: 从监听器接收到的dict，该dict将会经过此预处理器传入下游
    :param require_enable_modules: 是否需要检查模块是否已启用
    :param prefix: 使用的命令前缀。如果为None，则使用默认的命令前缀，存在''值的情况下则代表无需命令前缀
    :param running_mention: 消息内若包含机器人名称，则检查是否有命令正在运行
    :return: 无返回
    """
    try:
        modules = ModulesManager.return_modules_list_as_dict(msg.target.targetFrom)
        modulesAliases = ModulesManager.return_modules_alias_map()
        modulesRegex = ModulesManager.return_specified_type_modules(RegexCommand, targetFrom=msg.target.targetFrom)
        display = removeDuplicateSpace(msg.asDisplay())  # 将消息转换为一般显示形式
        identify_str = f'[{msg.target.senderId}{f" ({msg.target.targetId})" if msg.target.targetFrom != msg.target.senderFrom else ""}]'
        # Logger.info(f'{identify_str} -> [Bot]: {display}')
        msg.trigger_msg = display
        msg.target.senderInfo = senderInfo = BotDBUtil.SenderInfo(msg.target.senderId)
        enabled_modules_list = BotDBUtil.Module(msg).check_target_enabled_module_list()
        if len(display) == 0:
            return
        disable_prefix = False
        if prefix is not None:
            if '' in prefix:
                disable_prefix = True
            command_prefix.clear()
            command_prefix.extend(prefix)
        is_command = False
        if display[0] in command_prefix or disable_prefix:  # 检查消息前缀
            if len(display) <= 1 or (display[0] == '~' and display[1] == '~'):
                return
            is_command = True
            Logger.info(
                f'{identify_str} -> [Bot]: {display}')
            if disable_prefix and display[0] not in command_prefix:
                command = display
            else:
                command = display[1:]
            command_list = removeIneffectiveText(command_prefix, command.split('&&'))  # 并行命令处理
            if len(command_list) > 5 and not senderInfo.query.isSuperUser:
                return await msg.sendMessage('你不是本机器人的超级管理员，最多只能并排执行5个命令。')
            if not ExecutionLockList.check(msg):
                ExecutionLockList.add(msg)
            else:
                return await msg.sendMessage('您有命令正在执行，请稍后再试。')
            for command in command_list:
                command_spilt = command.split(' ')  # 切割消息
                msg.trigger_msg = command  # 触发该命令的消息，去除消息前缀
                command_first_word = command_spilt[0].lower()
                sudo = False
                mute = False
                if command_first_word == 'mute':
                    mute = True
                if command_first_word == 'sudo':
                    if not msg.checkSuperUser():
                        return await msg.sendMessage('你不是本机器人的超级管理员，无法使用sudo命令。')
                    sudo = True
                    del command_spilt[0]
                    command_first_word = command_spilt[0].lower()
                    msg.trigger_msg = ' '.join(command_spilt)
                if senderInfo.query.isInBlockList and not senderInfo.query.isInAllowList and not sudo:  # 如果是以 sudo 执行的命令，则不检查是否已 ban
                    return ExecutionLockList.remove(msg)
                in_mute = BotDBUtil.Muting(msg).check()
                if in_mute and not mute:
                    return ExecutionLockList.remove(msg)
                if command_first_word in modulesAliases:
                    command_spilt[0] = modulesAliases[command_first_word]
                    command = ' '.join(command_spilt)
                    command_spilt = command.split(' ')
                    command_first_word = command_spilt[0]
                    msg.trigger_msg = command
                if command_first_word in modules:  # 检查触发命令是否在模块列表中
                    try:
                        if enable_tos:
                            await temp_ban_check(msg)
                        module = modules[command_first_word]
                        if not isinstance(module, Command):
                            if module.desc is not None:
                                desc = f'介绍：\n{module.desc}'
                                if command_first_word not in enabled_modules_list:
                                    desc += f'\n{command_first_word}模块未启用，请发送~enable {command_first_word}启用本模块。'
                                await msg.sendMessage(desc)
                            continue
                        if module.required_superuser:
                            if not msg.checkSuperUser():
                                await msg.sendMessage('你没有使用该命令的权限。')
                                continue
                        elif not module.base:
                            if command_first_word not in enabled_modules_list and not sudo and require_enable_modules:  # 若未开启
                                await msg.sendMessage(f'{command_first_word}模块未启用，请发送~enable {command_first_word}启用本模块。')
                                continue
                        elif module.required_admin:
                            if not await msg.checkPermission():
                                await msg.sendMessage(f'{command_first_word}命令仅能被该群组的管理员所使用，请联系管理员执行此命令。')
                                continue
                        if not module.match_list.set:
                            await msg.sendMessage(ErrorMessage(f'{command_first_word}未绑定任何命令，请联系开发者处理。'))
                            continue
                        none_doc = True
                        for func in module.match_list.get(msg.target.targetFrom):
                            if func.help_doc is not None:
                                none_doc = False
                        if not none_doc:
                            try:
                                command_parser = CommandParser(module, msg=msg)
                                try:
                                    parsed_msg = command_parser.parse(msg.trigger_msg)
                                    submodule = parsed_msg[0]
                                    msg.parsed_msg = parsed_msg[1]
                                    if submodule.required_superuser:
                                        if not msg.checkSuperUser():
                                            await msg.sendMessage('你没有使用该命令的权限。')
                                            continue
                                    elif submodule.required_admin:
                                        if not await msg.checkPermission():
                                            await msg.sendMessage(
                                                f'此命令仅能被该群组的管理员所使用，请联系管理员执行此命令。')
                                            continue
                                    if not senderInfo.query.disable_typing:
                                        async with msg.Typing(msg):
                                            await parsed_msg[0].function(msg)  # 将msg传入下游模块
                                    else:
                                        await parsed_msg[0].function(msg)
                                except InvalidCommandFormatError:
                                    await msg.sendMessage('语法错误。\n' + command_parser.return_formatted_help_doc())
                                    continue
                            except InvalidHelpDocTypeError:
                                Logger.error(traceback.format_exc())
                                await msg.sendMessage(ErrorMessage(f'{command_first_word}模块的帮助信息有误，请联系开发者处理。'))
                                continue
                        else:
                            msg.parsed_msg = None
                            for func in module.match_list.set:
                                if func.help_doc is None:
                                    if not senderInfo.query.disable_typing:
                                        async with msg.Typing(msg):
                                            await func.function(msg)  # 将msg传入下游模块
                                    else:
                                        await func.function(msg)
                    except ActionFailed:
                        await msg.sendMessage('消息发送失败，可能被风控，请稍后再试。')
                        continue
                    except FinishedException as e:
                        Logger.info(f'Successfully finished session from {identify_str}, returns: {str(e)}')
                        if msg.target.targetFrom != 'QQ|Guild' or command_first_word != 'module' and enable_tos:
                            await msg_counter(msg, msg.trigger_msg)
                        continue
                    except Exception as e:
                        Logger.error(traceback.format_exc())
                        ExecutionLockList.remove(msg)
                        await msg.sendMessage(ErrorMessage('执行命令时发生错误，请报告机器人开发者：\n' + str(e)))
                        continue
            ExecutionLockList.remove(msg)
            return msg
        if not is_command:
            if running_mention:
                if display.find('小可') != -1:
                    if ExecutionLockList.check(msg):
                        return await msg.sendMessage('您先前的命令正在执行中。')
            for regex in modulesRegex:  # 遍历正则模块列表
                try:
                    if regex in enabled_modules_list:
                        regex_module = modulesRegex[regex]
                        if regex_module.required_superuser:
                            if not msg.checkSuperUser():
                                continue
                        elif regex_module.required_admin:
                            if not await msg.checkPermission():
                                continue
                        for rfunc in regex_module.match_list.set:
                            msg.matched_msg = False
                            matched = False
                            if rfunc.mode.upper() in ['M', 'MATCH']:
                                msg.matched_msg = re.match(rfunc.pattern, display, flags=rfunc.flags)
                                if msg.matched_msg is not None:
                                    matched = True
                            elif rfunc.mode.upper() in ['A', 'FINDALL']:
                                msg.matched_msg = re.findall(rfunc.pattern, display, flags=rfunc.flags)
                                if msg.matched_msg:
                                    matched = True
                            if matched:
                                if regex_module.required_superuser:
                                    if not msg.checkSuperUser():
                                        continue
                                elif regex_module.required_admin:
                                    if not await msg.checkPermission():
                                        continue
                                if not ExecutionLockList.check(msg):
                                    ExecutionLockList.add(msg)
                                else:
                                    return await msg.sendMessage('您有命令正在执行，请稍后再试。')
                                if rfunc.show_typing and not senderInfo.query.disable_typing:
                                    async with msg.Typing(msg):
                                        await rfunc.function(msg)  # 将msg传入下游模块
                                else:
                                    await rfunc.function(msg)  # 将msg传入下游模块
                            ExecutionLockList.remove(msg)
                except ActionFailed:
                    await msg.sendMessage('消息发送失败，可能被风控，请稍后再试。')
                    continue
                except FinishedException as e:
                    Logger.info(
                        f'Successfully finished session from {identify_str}, returns: {str(e)}')
                    if enable_tos:
                        await msg_counter(msg, msg.trigger_msg)
                    continue
                ExecutionLockList.remove(msg)
            return msg
    except AbuseWarning as e:
        if enable_tos:
            await warn_target(msg, str(e))
            temp_ban_counter[msg.target.senderId] = {'count': 1,
                                                     'ts': datetime.now().timestamp()}
            return
    except WaitCancelException:
        Logger.info('Waiting task cancelled by user.')
    except Exception:
        Logger.error(traceback.format_exc())
    ExecutionLockList.remove(msg)
