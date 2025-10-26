import itertools
import json
import logging
import math
import os
import time
from collections import defaultdict, Counter

from RChart import Chart, MusicDB
from RDeck import Rarity
from Simulator_core import DB_CARDDATA, DB_SKILL
from SkillResolver import SkillEffectType
logger = logging.getLogger(__name__)

CARD_CONFLICT_RULES = {
    # P吟、Blast芽、暧昧Mayday、水果帆、水果吟、太阳沙、COCO夏芽
    1031530: {1041513, 1042515, 1043515, 1031531, 1041516, 1032529, 1043516},  # idome帆
    1032528: {1041513, 1042515, 1043515, 1031531, 1041516, 1032529, 1043516},  # idome沙
    1033524: {1041513, 1042515, 1043515, 1031531, 1041516, 1032529, 1043516},  # idome乃
}


def has_card_conflict(card_ids_in_deck: set[int]) -> bool:
    """
    检查卡组中是否存在冲突卡牌。
    """
    for restricted_card_id, conflicting_ids in CARD_CONFLICT_RULES.items():
        if restricted_card_id in card_ids_in_deck:
            if any(c_id in card_ids_in_deck for c_id in conflicting_ids):
                return True
    return False


DB_TAG = {}
"""
卡牌id -> 技能效果类型、稀有度
多段同类效果只记录一个tag
"""
for data in DB_CARDDATA.values():
    skill_series_id = data["RhythmGameSkillSeriesId"][-1]
    skill_effect = DB_SKILL[str(skill_series_id * 100 + 14)]["RhythmGameSkillEffectId"]
    tag = set()
    for effect in skill_effect:
        tag.add(SkillEffectType(effect // 100000000))
    tag.add(Rarity(data["Rarity"]))
    DB_TAG[data["CardSeriesId"]] = tag


def count_skill_tags(card_ids_input: list[int]):
    """
    计算给定卡牌ID列表中所有卡牌的技能tag的出现次数。

    Args:
        card_ids_input (list[int]): 包含6个卡牌ID的列表。

    Returns:
        collections.Counter: 一个Counter对象，键是tag，值是其出现次数。
    """
    # 直接使用Counter，避免创建中间列表
    tag_counts = Counter()

    for card_id in card_ids_input:
        if card_id in DB_TAG:
            # 将当前卡牌的所有tag（一个集合）累加到Counter中
            tag_counts.update(DB_TAG[card_id])

    return tag_counts


def generate_role_distributions(all_characters):
    """
    生成6个卡位的角色分布，允许部分角色双卡。
    返回的每个分布是一个长度6的角色ID列表，可能包含重复（表示双卡）。
    """
    results = []

    # 0,1,2,3个角色双卡
    for double_count in range(0, 4):
        for doubles in itertools.combinations(all_characters, double_count):
            remaining_roles_needed = 6 - 2 * double_count
            for singles in itertools.combinations(
                [r for r in all_characters if r not in doubles],
                remaining_roles_needed
            ):
                distribution = list(doubles) * 2 + list(singles)
                results.append(tuple(sorted(distribution)))

    return list(set(results))


def load_simulated_decks(path: str):
    simulated_decks = set()
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for result in data:
            current_deck_card_ids = result['deck_card_ids']
            sorted_card_ids_tuple = tuple(sorted(current_deck_card_ids))
            simulated_decks.add(sorted_card_ids_tuple)
        logger.info(f"{len(simulated_decks)} simulation results loaded.")
    return simulated_decks


class DeckGeneratorWithDoubleCards:
    def __init__(self, cardpool: list[int], mustcards: list[list[int]], center_char=None, force_dr=False, log_path: str = None):
        self.cardpool = cardpool
        self.center_char = center_char
        self.char_id_to_cards = defaultdict(list)
        self.force_dr = force_dr
        self.mustcards = mustcards
        self.simulated_decks = load_simulated_decks(log_path)
        for card_id in self.cardpool:
            char_id = card_id // 1000
            self.char_id_to_cards[char_id].append(card_id)
        self.all_available_chars = list(self.char_id_to_cards.keys())

        # 预计算数量
        self.total_decks = self.compute_total_count()

    def __iter__(self):
        if len(self.all_available_chars) < 3:
            return
        for char_distribution in generate_role_distributions(self.all_available_chars):
            if self.center_char and self.center_char not in char_distribution:
                continue
            yield from self._generate_decks_for_distribution(char_distribution)

    def check_skill_tags(self, tag_counts: Counter, force_dr=False):
        """
        检查卡组中的技能类型是否符合给定条件。
        默认检查洗牌、分、电、分加成、电加成均不为0，且DR数量<=1。
        """
        if all(tag_counts[skill] for skill in self.mustcards[2]) and \
                tag_counts[Rarity.DR] <= 1:
            if not force_dr:
                return True
            elif tag_counts[Rarity.DR] == 1:
                return True
        return False

    def _generate_valid_permutations(self, deck):
        """
        PyPy 优化：

        规则：
        - 第一位不能是分卡 (ScoreGain)
        - 最后一位不能是洗牌卡 (DeckReset)
        """
        # 预先分类卡牌
        score_gain_cards = set()
        deck_reset_cards = set()

        for card_id in deck:
            tags = DB_TAG[card_id]
            if SkillEffectType.ScoreGain in tags:
                score_gain_cards.add(card_id)
            if SkillEffectType.DeckReset in tags:
                deck_reset_cards.add(card_id)

        # PyPy JIT 高度优化 itertools.permutations
        # 全排列+过滤比嵌套循环更快
        for perm in itertools.permutations(deck):
            # 第一位不能是分卡
            if perm[0] in score_gain_cards:
                continue
            # 最后一位不能是洗牌卡
            if perm[-1] in deck_reset_cards:
                continue
            yield perm

    def _count_valid_permutations(self, deck):
        """
        计算有效排列的数量，用于预估总卡组数。
        PyPy 优化版本：使用简化的数学公式
        """
        # 分类卡牌
        score_gain_count = 0
        deck_reset_count = 0

        for card_id in deck:
            tags = DB_TAG[card_id]
            if SkillEffectType.ScoreGain in tags:
                score_gain_count += 1
            if SkillEffectType.DeckReset in tags:
                deck_reset_count += 1

        # 计算有效排列数
        # 第一位可选数量 = 总数 - 分卡数量
        valid_first = 6 - score_gain_count
        if valid_first == 0:
            return 0

        # 最后一位可选数量 = 总数 - 洗牌卡数量 - 1(已被第一位占用)
        valid_last = 6 - deck_reset_count - 1

        if valid_last <= 0:
            return 0

        # 中间4位的排列数 = 4!
        middle_perms = math.factorial(4)

        # 总有效排列数 = 第一位选择 × 最后一位选择 × 中间排列
        # 这是一个近似值，实际可能略有不同，但用于预估足够
        return valid_first * valid_last * middle_perms

    def _generate_decks_for_distribution(self, char_distribution):
        char_counts = {char_id: char_distribution.count(char_id) for char_id in set(char_distribution)}
        card_choices_per_char = []
        for char_id, count in char_counts.items():
            card_pool = self.char_id_to_cards[char_id]
            if count == 1:
                card_choices_per_char.append([(card_id,) for card_id in card_pool])
            elif count == 2:
                card_choices_per_char.append(list(itertools.combinations(card_pool, 2)))
            else:
                raise ValueError("角色数量超过2，不符合规则")

        for combo in itertools.product(*card_choices_per_char):
            deck = []
            for item in combo:
                deck.extend(item)
            if tuple(sorted(deck)) in self.simulated_decks:
                continue
            if self.mustcards[0]:
                if not all(card in deck for card in self.mustcards[0]):
                    continue
            if self.mustcards[1]:
                if not any(card in deck for card in self.mustcards[1]):
                    continue
            if has_card_conflict(set(deck)):
                continue
            if self.check_skill_tags(count_skill_tags(deck), self.force_dr):
                # 使用优化的排列生成器，避免生成无效排列
                yield from self._generate_valid_permutations(deck)

    def _count_decks_for_distribution(self, char_distribution):
        char_counts = {char_id: char_distribution.count(char_id) for char_id in set(char_distribution)}
        card_choices_per_char = []
        for char_id, count in char_counts.items():
            card_pool = self.char_id_to_cards[char_id]
            if count == 1:
                card_choices_per_char.append([(card_id,) for card_id in card_pool])
            elif count == 2:
                card_choices_per_char.append(list(itertools.combinations(card_pool, 2)))
            else:
                raise ValueError("角色数量超过2，不符合规则")

        total = 0
        for combo in itertools.product(*card_choices_per_char):
            deck = []
            for item in combo:
                deck.extend(item)
            if tuple(sorted(deck)) in self.simulated_decks:
                continue
            if self.mustcards[0]:
                if not all(card in deck for card in self.mustcards[0]):
                    continue
            if self.mustcards[1]:
                if not any(card in deck for card in self.mustcards[1]):
                    continue
            if has_card_conflict(set(deck)):
                continue
            if self.check_skill_tags(count_skill_tags(deck), self.force_dr):
                # 使用优化的计数方法
                total += self._count_valid_permutations(deck)
        return total

    def compute_total_count(self):
        total = 0
        if len(self.all_available_chars) < 3:
            return 0
        for char_distribution in generate_role_distributions(self.all_available_chars):
            if self.center_char and self.center_char not in char_distribution:
                continue
            total += self._count_decks_for_distribution(char_distribution)
        return total


def generate_decks_with_double_cards(cardpool: list[int], mustcards: list[list[int]], center_char: int = None, force_dr: bool = False, log_path: str = None):
    """
    外部接口函数，返回支持双卡规则的卡组生成器
    """
    return DeckGeneratorWithDoubleCards(cardpool, mustcards, center_char, force_dr, log_path)


if __name__ == "__main__":
    card_ids = [
        1011501,  # 沙知
        1021523, 1021901, 1021512, 1021701,  # 梢: 银河 BR 舞会 LR
        1022521, 1022701, 1022901, 1022504,  # 缀: 银河 LR BR 明月
        1023520, 1023701, 1023901,  # 慈: 银河 LR BR
        1031519, 1031530, 1031901,  # 帆: 舞会 IDOME BR(2024)
        1032518, 1032528, 1032901,  # 沙: 舞会 IDOME BR
        1033514, 1033524, 1033901,  # 乃: 舞会 IDOME BR
        1041513,  # 吟: 舞会
        # 1042515, # 1042512,  # 铃: 暧昧mayday 舞会
        1043515,  # 芽: BLAST 舞会1043512
        # 1051503, #1051501, 1051502,  # 泉: 天地黎明 DB RF
        1052901, 1052503,  # 1052504  # 塞: BR 十六夜 天地黎明
    ]

    MUSIC_DB = MusicDB()
    fixed_music_id = "405302"  # aiscream
    fixed_difficulty = "02"
    pre_initialized_chart = Chart(MUSIC_DB, fixed_music_id, fixed_difficulty)

    decks_generator = generate_decks_with_double_cards(
        card_ids, pre_initialized_chart.music.CenterCharacterId
    )
    total_decks_to_simulate = decks_generator.total_decks
    print(f"预计算总共将模拟 {total_decks_to_simulate} 个卡组。")

    time_list = []
    for _ in range(10):
        start_time = time.time()
        for deck in decks_generator:
            pass
        end_time = time.time()
        time_list.append(end_time - start_time)
    print(f"avg: {sum(time_list)/len(time_list)}")
