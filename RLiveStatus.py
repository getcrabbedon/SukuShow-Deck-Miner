import logging
from functools import lru_cache
from platform import python_implementation
from RDeck import Deck

logger = logging.getLogger(__name__)

pypy_impl = python_implementation() == "PyPy"
if pypy_impl:
    def ceil(x):
        return int(x) + (x > int(x))
else:
    from math import ceil

# Numba优化被禁用（字典查找开销抵消了优势）
NUMBA_AVAILABLE = False


class Voltage:
    """
    管理Voltage点数和对应的Voltage等级。

    等级计算规则：
    从 L 级升到 L+1 级需要额外的 (L+1) * 10 点 VoltagePt。
    例如：
    0级到1级需要 1 * 10 = 10 Pt (总计10 Pt达到1级)
    1级到2级需要 2 * 10 = 20 Pt (总计10+20=30 Pt达到2级)
    N <= 20时，达到 N 级总共需要的点数为 5 * N * (N + 1)。
    N >= 20时，固定为每200 Pt一级。
    """

    def __init__(self, initial_points: int = 0):
        if not isinstance(initial_points, int) or initial_points < 0:
            raise ValueError("初始 VoltagePt 必须是非负整数。")

        self._current_points = 0  # 内部存储实际点数
        self._current_level = 0  # 内部存储当前等级

        # 设置初始点数并计算初始等级
        self.set_points(initial_points)

    # 辅助函数：计算达到某个等级所需的总点数
    # 使用 lru_cache 装饰器进行结果缓存
    @staticmethod
    @lru_cache(maxsize=None)
    def _points_needed_for_level(level: int) -> int:
        if level <= 0:
            return 0
        # N级所需总点数 = 10 * (1 + 2 + ... + N) = 10 * N * (N + 1) / 2 = 5 * N * (N + 1)
        elif level <= 20:
            return 5 * level * (level + 1)
        else:
            return level * 200 - 1900

    def _update_level(self):
        """
        根据当前点数和上次的等级，高效地更新 Voltage 等级。
        这个方法在点数被 `add_points` 或 `set_points` 修改后调用。
        """
        old_level = self._current_level

        # 优化：从当前等级开始向上或向下检查

        # 1. 检查是否可以升级
        # 如果当前点数满足下一等级的起始点数，则尝试升级
        while self._current_points >= Voltage._points_needed_for_level(self._current_level + 1):
            self._current_level += 1

        # 2. 检查是否需要降级
        # 如果当前点数低于当前等级的起始点数 (但不是0级)，则尝试降级
        # 注意：0级不需要降级，因为_points_needed_for_level(0) = 0
        while self._current_points < Voltage._points_needed_for_level(self._current_level) and self._current_level > 0:
            self._current_level -= 1

        if logger.isEnabledFor(logging.DEBUG):
            if old_level != self._current_level:
                logger.debug(f"  Voltage等级变化: 从 Lv.{old_level} -> Lv.{self._current_level}")

    def add_points(self, amount: int):
        """
        增加 Voltage 点数，并自动更新等级。
        """
        if not isinstance(amount, int):
            raise ValueError("增加的 VoltagePt 必须是整数。")

        self._current_points += amount
        if self._current_points < 0:  # VoltagePt不能低于0
            self._current_points = 0
        self._update_level()

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"  VoltagePt {'+' if amount >= 0 else '-'}{abs(amount)} 点 (当前: {self._current_points} Pt)")

    def set_points(self, new_points: int):
        """
        直接设置 Voltage 点数，并自动更新等级。
        """
        if not isinstance(new_points, int) or new_points < 0:
            raise ValueError("设置的 VoltagePt 必须是非负整数。")

        old_points = self._current_points
        self._current_points = new_points
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"  VoltagePt 从 {old_points} -> {self._current_points} 点")
        self._update_level()

    def get_points(self) -> int:
        """
        获取当前的 Voltage 点数。
        """
        return self._current_points

    def get_level(self) -> int:
        """
        获取当前的 Voltage 等级。
        """
        return self._current_level

    def __str__(self):
        """
        Voltage 对象的字符串表示。
        """
        return f"Voltage: {self._current_points} Pt (Lv.{self._current_level})"

class MentalDown(Exception):
    def __init__(self):
        pass

class Mental:
    def __init__(self) -> None:
        self.current_hp: int = 100
        self.max_hp: int = 100
        self.badMinus: int = 30
        self.missMinus: int = 50
        self.traceMinus: int = 20

    def set_hp(self, hp: int):
        self.max_hp = hp
        self.current_hp = self.max_hp
        self.badMinus += int(self.max_hp * 0.03)
        self.missMinus += int(self.max_hp * 0.05)
        self.traceMinus += int(self.max_hp * 0.02)

    def sub(self, value, note_type=None):
        if type(value) is int:
            # 输入要扣除的血量百分比
            self.current_hp = max(0, self.current_hp - ceil(self.max_hp * value * 0.01))
        elif type(value) is str:
            # 输入判定 - 优化：使用if-elif代替match
            if value == "MISS":
                if note_type == "Trace" or note_type == "HoldMid":
                    self.current_hp = max(0, self.current_hp - self.traceMinus)
                else:
                    self.current_hp = max(0, self.current_hp - self.missMinus)
            elif value == "BAD":
                self.current_hp = max(0, self.current_hp - self.badMinus)
            else:
                return  # 其他判定不扣血

            if self.current_hp:
                return
            raise MentalDown()

    def skill_add(self, value):
        self.current_hp = max(1, self.current_hp + ceil(self.max_hp * value * 0.01))  # 优化：* 0.01 代替 / 100

    def get_rate(self):
        return self.current_hp * 100.0 / self.max_hp

    def __str__(self):
        """
        Mental 对象的字符串表示。
        """
        return f"Mental: {self.current_hp} / {self.max_hp} ({self.get_rate():.2f}%)"


class PlayerAttributes:
    """
    模拟游戏中的玩家属性
    """

    def __init__(self, masterlv=1):
        self.ap = 0            # 初始AP
        self.cooldown = 5.0     # 初始技能冷却时间
        self.ap_rate = 1
        self.combo = 0
        self.ap_gain_rate = 100
        self.voltage_gain_rate = 100
        self.mental = Mental()
        self.score = 0
        self.voltage = Voltage(0)
        self.next_score_gain_rate = []
        self.next_voltage_gain_rate = []
        self.fevertime = False
        self.CDavailable = False
        self.deck: Deck
        self.appeal: int = 0
        self.masterlv: int = masterlv
        self.base_score: float
        self.note_score: dict = dict()
        self.half_ap_plus: float
        self.full_ap_plus: float

    def __str__(self) -> str:
        return (
            f"当前属性:\n"
            f"  AP: {self.ap:.5f}  Combo: {self.combo}\t"
            f"AP Gain Rate: {self.ap_rate:.2f}x\t"
            f"{self.mental}\n"
            f"  Score: {self.score}\t"
            f"{self.voltage}\t"
            f"分加成: {self.next_score_gain_rate}\t"
            f"电加成: {self.next_voltage_gain_rate}\t"
        )

    def __str_full__(self):
        return (
            f"当前属性:\n"
            f"  AP: {self.ap:.5f}  Combo: {self.combo}\n"
            f"  Cooldown: {self.cooldown:.1f}s\n"
            f"  AP Gain Rate: {self.ap_rate:.2f}x\n"
            f"  Voltage Gain Rate: {self.voltage_gain_rate:.2f}%\n"
            f"  {self.mental}\n"
            f"  Score: {self.score}\n"
            f"  {self.voltage}\n"
            f"  Next Score Gain Rate: {self.next_score_gain_rate}\n"
            f"  Next Voltage Gain Rate: {self.next_voltage_gain_rate}\n"
        )

    def set_deck(self, deck: Deck):
        self.deck = deck
    
    def hp_calc(self):
        self.mental.set_hp(self.deck.mental_calc())

    def basescore_calc(self, all_note_size: int):
        masterlv_bonus = self.masterlv / 100 + 1
        self.base_score = self.deck.appeal * masterlv_bonus
        self.note_score = {
            "PERFECT": 30 * self.base_score / all_note_size,
            "GREAT": 25 * self.base_score / all_note_size,
            "GOOD": 15 * self.base_score / all_note_size,
            "BAD": 5 * self.base_score / all_note_size,
            "MISS": 0
        }
        self.half_ap_plus = 300000 / all_note_size
        self.full_ap_plus = 600000 / all_note_size

    def score_add(self, value, skill=True):
        voltage_lv = self.voltage.get_level()
        if self.fevertime:
            voltage_lv *= 2
        voltage_bonus = (voltage_lv + 10) / 10
        value *= voltage_bonus
        if skill:
            value *= self.base_score
        value = ceil(value)
        self.score += value
        return value

    def score_note(self, judgement):
        score_value = self.note_score[judgement]
        return self.score_add(score_value, skill=False)

    def combo_add(self, judgement, note_type=None):
        self.combo += 1
        if self.combo <= 50:
            self.ap_rate = 1.0 + (self.combo // 10) * 0.1
        match judgement:
            case "PERFECT" | "GREAT":
                self.ap += ceil(self.full_ap_plus * self.ap_rate) * 0.0001
                self.score_note(judgement)
            case "GOOD":
                self.ap += ceil(self.half_ap_plus * self.ap_rate) * 0.0001
                self.score_note(judgement)
            case _:  # BAD or MISS
                self.combo = 0
                self.ap_rate = 1.0
                self.mental.sub(judgement, note_type)
                if judgement == "BAD":  # 只有BAD计分，MISS不计分
                    self.score_note(judgement)


if __name__ == "__main__":
    print(Voltage._points_needed_for_level(247))
    print(Voltage._points_needed_for_level(248))
