/*
 * Spark fixture site — question sets used by questions.html.
 *
 * Every answer here is derivable only from the text in passage-data.js
 * (mirrored in answer-key.json), not from outside knowledge, since
 * "Millbrook" is a fictional town invented for this fixture.
 */
var POINTS_PER_CORRECT = 20;

var QUESTION_SETS = {
  "1": [
    {
      id: "q1",
      question: "According to the passage, who founded Millbrook?",
      options: [
        { id: "a", text: "Edmund Carter" },
        { id: "b", text: "Josiah Carter" },
        { id: "c", text: "Louisa Carter Whitfield" },
        { id: "d", text: "A local farmer" }
      ],
      correct: "a"
    },
    {
      id: "q2",
      question: "In what year did Edmund Carter travel to the site that would become Millbrook?",
      options: [
        { id: "a", text: "1837" },
        { id: "b", text: "1847" },
        { id: "c", text: "1861" },
        { id: "d", text: "1889" }
      ],
      correct: "b"
    },
    {
      id: "q3",
      question: "How many buildings were destroyed in the 1889 fire?",
      options: [
        { id: "a", text: "Twelve" },
        { id: "b", text: "Nineteen" },
        { id: "c", text: "Twenty-six" },
        { id: "d", text: "Forty" }
      ],
      correct: "c"
    },
    {
      id: "q4",
      question: "Who donated the funds that paid for the Millbrook clocktower?",
      options: [
        { id: "a", text: "Edmund Carter" },
        { id: "b", text: "Josiah Carter" },
        { id: "c", text: "The town council" },
        { id: "d", text: "Louisa Carter Whitfield" }
      ],
      correct: "d"
    },
    {
      id: "q5",
      question: "What is the name of the annual festival held each September in Millbrook?",
      options: [
        { id: "a", text: "The Founders Fair" },
        { id: "b", text: "The Millrace Festival" },
        { id: "c", text: "The Harvest Parade" },
        { id: "d", text: "The Clocktower Gala" }
      ],
      correct: "b"
    }
  ],

  "2": [
    {
      id: "q1",
      question: "What river was Millbrook built beside?",
      options: [
        { id: "a", text: "Coldwater Creek" },
        { id: "b", text: "The Silver Fork River" },
        { id: "c", text: "The Millrace River" },
        { id: "d", text: "The Whitfield River" }
      ],
      correct: "b"
    },
    {
      id: "q2",
      question: "How many feet did the river drop over the granite bed that Carter chose for his mill?",
      options: [
        { id: "a", text: "Twenty feet" },
        { id: "b", text: "Thirty feet" },
        { id: "c", text: "Forty feet" },
        { id: "d", text: "Sixty feet" }
      ],
      correct: "c"
    },
    {
      id: "q3",
      question: "In what year did Josiah Carter add a grain elevator beside the original mill?",
      options: [
        { id: "a", text: "1856" },
        { id: "b", text: "1861" },
        { id: "c", text: "1874" },
        { id: "d", text: "1889" }
      ],
      correct: "c"
    },
    {
      id: "q4",
      question: "How tall is the Millbrook clocktower?",
      options: [
        { id: "a", text: "Forty feet" },
        { id: "b", text: "Sixty feet" },
        { id: "c", text: "Eighty-two feet" },
        { id: "d", text: "One hundred feet" }
      ],
      correct: "c"
    },
    {
      id: "q5",
      question: "On which two days is the Millbrook Historical Society museum open?",
      options: [
        { id: "a", text: "Monday and Friday" },
        { id: "b", text: "Wednesday and Saturday" },
        { id: "c", text: "Tuesday and Thursday" },
        { id: "d", text: "Saturday and Sunday" }
      ],
      correct: "b"
    }
  ]
};
