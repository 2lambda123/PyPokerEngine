from pypoker2.engine.table import Table
from pypoker2.engine.action import Action
from pypoker2.engine.action_checker import ActionChecker
from pypoker2.engine.game_evaluator import GameEvaluator
from pypoker2.engine.message_builder import MessageBuilder

class RoundManager:

  PREFLOP = 0
  FLOP = 1
  TURN = 2
  RIVER = 3
  SHOWDOWN = 4
  FINISHED = 5

  @classmethod
  def start_new_round(self, round_count, table):
    _state = self.__gen_initial_state(round_count, table)
    state = self.__deep_copy_state(_state)
    table = state["table"]

    table.deck.shuffle
    self.__correct_blind(5, table)  # TODO read from config
    self.__deal_holecard(table.deck, table.seats.players)
    start_msg = self.__round_start_message(round_count, table)
    state, street_msgs = self.__start_street(state)
    return state, start_msg + street_msgs

  @classmethod
  def apply_action(self, original_state, action, bet_amount):
    state = self.__deep_copy_state(original_state)
    state = self.__update_state_by_action(state, action, bet_amount)
    update_msg = self.__update_message(state, action, bet_amount)
    if self.__is_everyone_agreed(state):
      state["street"] += 1
      state = self.__clear_action_histories(state)
      state, street_msgs = self.__start_street(state)
      return state, [update_msg] + street_msgs
    else:
      state["next_player"] = state["table"].next_active_player_pos(state["next_player"])
      next_player_pos = state["next_player"]
      next_player = state["table"].seats.players[next_player_pos]
      ask_message = (next_player.uuid, MessageBuilder.ask_message(next_player_pos, None, state["table"]))
      return state, [update_msg, ask_message]


  @classmethod
  def __correct_blind(self, sb_amount, table):
    small_blind_pos = table.dealer_btn
    big_blind_pos = table.next_active_player_pos(small_blind_pos)
    self.__blind_transaction(table.seats.players[small_blind_pos], True, sb_amount)
    self.__blind_transaction(table.seats.players[big_blind_pos], False, sb_amount*2)

  @classmethod
  def __blind_transaction(self, player, small_blind, blind_amount):
    action = Action.SMALL_BLIND if small_blind else Action.BIG_BLIND
    player.collect_bet(blind_amount)
    player.add_action_history(action)
    player.pay_info.update_by_pay(blind_amount)

  @classmethod
  def __deal_holecard(self, deck, players):
    for player in players:
      player.add_holecard(deck.draw_cards(2))

  @classmethod
  def __start_street(self, state):
    state["agree_num"] = 0
    state["next_player"] = state["table"].dealer_btn
    street = state["street"]
    if street == self.PREFLOP:
      return self.__preflop(state)
    elif street == self.FLOP:
      return self.__flop(state)
    elif street == self.TURN:
      return self.__turn(state)
    elif street == self.RIVER:
      return self.__river(state)
    elif street == self.SHOWDOWN:
      return self.__showdown(state)
    else:
      raise ValueError("Street is already finished [street = %d]" % street)

  @classmethod
  def __preflop(self, state):
    for i in range(2):
      state["next_player"] = state["table"].next_active_player_pos(state["next_player"])
    state["agree_num"] = 1  # big blind already agreed
    return self.__forward_street(state)

  @classmethod
  def __flop(self, state):
    for card in state["table"].deck.draw_cards(3):
      state["table"].add_community_card(card)
    return self.__forward_street(state)

  @classmethod
  def __turn(self, state):
    state["table"].add_community_card(state["table"].deck.draw_card())
    return self.__forward_street(state)

  @classmethod
  def __river(self, state):
    state["table"].add_community_card(state["table"].deck.draw_card())
    return self.__forward_street(state)

  @classmethod
  def __showdown(self, state):
    winners, prize_map = GameEvaluator.judge(state["table"])
    self.__prize_to_winners(state["table"].seats.players, prize_map)
    state["table"].reset()
    state["street"] += 1
    result_message = (-1, MessageBuilder.round_result_message(state, winners))
    return state, [result_message]

  @classmethod
  def __prize_to_winners(self, players, prize_map):
    for idx, prize in prize_map.items():
      players[idx].append_chip(prize)

  @classmethod
  def __round_start_message(self, round_count, table):
    players = table.seats.players
    gen_msg = lambda idx: (players[idx].uuid, MessageBuilder.round_start_message(round_count, idx, table.seats))
    return reduce(lambda acc, idx: acc + [gen_msg(idx)], range(len(players)), [])

  @classmethod
  def __forward_street(self, state):
    table = state["table"]
    street_start_msg = (-1, MessageBuilder.street_start_message(None, table))
    if table.seats.count_ask_wait_players == 1:
      state["street"] += 1
      state, messages = self.__start_street(state)
      return state, [street_start_mst] + messages
    else:
      next_player_pos = state["next_player"]
      next_player = table.seats.players[next_player_pos]
      ask_message = (next_player.uuid, MessageBuilder.ask_message(next_player_pos, None, table))
      return state, [street_start_msg, ask_message]

  @classmethod
  def __update_state_by_action(self, state, action, bet_amount):
    table = state["table"]
    action, bet_amount = ActionChecker.correct_action(\
        table.seats.players, state["next_player"], action, bet_amount)
    next_player = table.seats.players[state["next_player"]]
    if ActionChecker.is_allin(next_player, action, bet_amount):
      next_player.pay_info.update_to_allin()
    return self.__accept_action(state, action, bet_amount)

  @classmethod
  def __accept_action(self, state, action, bet_amount):
    player = state["table"].seats.players[state["next_player"]]
    if action == 'call':
      self.__chip_transaction(player, bet_amount)
      player.add_action_history(Action.CALL, bet_amount)
      state["agree_num"] += 1
    elif action == 'raise':
      self.__chip_transaction(player, bet_amount)
      add_amount = bet_amount - ActionChecker.agree_amount(state["table"].seats.players)
      player.add_action_history(Action.RAISE, bet_amount, add_amount)
    elif action == 'fold':
      player.add_action_history(Action.FOLD)
      player.pay_info.update_to_fold()
    else:
      raise ValueError("Unexpected action %s received" % action)
    return state

  @classmethod
  def __chip_transaction(self, player, bet_amount):
    need_amount = ActionChecker.need_amount_for_action(player, bet_amount)
    player.collect_bet(need_amount)
    player.pay_info.update_by_pay(need_amount)

  @classmethod
  def __update_message(self, state, action, bet_amount):
    return (-1, MessageBuilder.game_update_message(state, action, bet_amount))

  @classmethod
  def __is_everyone_agreed(self, state):
    return state["agree_num"] == state["table"].seats.count_active_players()

  @classmethod
  def __clear_action_histories(self, state):
    for player in state["table"].seats.players:
      player.clear_action_histories()
    return state


  @classmethod
  def __gen_initial_state(self, round_count, table):
    return {
        "street": self.PREFLOP,
        "agree_num": 0,
        "next_player": table.dealer_btn,
        "table": table
    }

  @classmethod
  def __deep_copy_state(self, state):
    table_deepcopy = Table.deserialize(state["table"].serialize())
    return {
        "street": state["street"],
        "agree_num": state["agree_num"],
        "next_player": state["next_player"],
        "table": table_deepcopy
        }