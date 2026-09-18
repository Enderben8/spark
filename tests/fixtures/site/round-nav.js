/*
 * Spark fixture site — round/set navigation helpers.
 *
 * The fixture flow is meant to repeat indefinitely so loop-until-target
 * tests have something to loop against:
 *
 *   login.html -> passage.html?round=N -> questions.html?set=S&round=N
 *              -> score.html?round=N -> (continue) -> passage.html?round=N+1
 *
 * The question set alternates 1, 2, 1, 2, ... across rounds so a multi-round
 * run exercises both question sets in answer-key.json.
 */
(function (global) {
  "use strict";

  function getRound() {
    var params = new URLSearchParams(window.location.search);
    var r = parseInt(params.get("round"), 10);
    if (isNaN(r) || r < 1) {
      return 1;
    }
    return r;
  }

  function getSetForRound(round) {
    return ((round - 1) % 2) + 1;
  }

  function questionsHrefForRound(round) {
    return "questions.html?set=" + getSetForRound(round) + "&round=" + round;
  }

  function passageHrefForRound(round) {
    return "passage.html?round=" + round;
  }

  global.getRound = getRound;
  global.getSetForRound = getSetForRound;
  global.questionsHrefForRound = questionsHrefForRound;
  global.passageHrefForRound = passageHrefForRound;
})(window);
