/*
 * Spark fixture site — the passage text, as a shared JS data file.
 *
 * passage.html, passage-lazy.html, and the three canvas variants all use
 * this exact text so that every rendering path (plain DOM, lazy DOM, canvas,
 * condensed canvas, two-column canvas) is testing comprehension of the same
 * underlying facts. answer-key.json mirrors this text for the test suite.
 *
 * The story (Millbrook, its founder, its fire, its clocktower) is entirely
 * fictional but internally consistent, so every question below is
 * answerable only from this text, not from general knowledge.
 */
var PASSAGE_TITLE = "The Founding of Millbrook";

var PASSAGE_PARAGRAPHS = [
  "In 1847, a millwright named Edmund Carter traveled west along the Silver Fork River in search of a site for a new gristmill. He found what he needed roughly two miles north of the river's confluence with Coldwater Creek: a stretch where the river dropped forty feet over a bed of exposed granite, more than enough fall to turn a large waterwheel. Carter purchased sixty acres from a local farmer, built a timber dam, and by the following spring had his first mill wheel turning. The settlement that grew up around the mill took its name, Millbrook, from the small brook that fed the millpond behind the dam.",

  "Word of steady work drew families from the surrounding countryside, and Millbrook grew quickly through the 1850s. A wool carding mill opened in 1856 to serve the sheep farmers of the outlying hills, and a second gristmill followed soon after. By 1861, the town's population had reached roughly nine hundred people, and Millbrook had its own post office, a one-room schoolhouse, and a weekly newspaper called the Millbrook Courier. In 1874, Carter's eldest son, Josiah, added a grain elevator beside the original mill, allowing farmers from as far as twenty miles away to store and sell their wheat without hauling it to the county seat.",

  "Disaster struck on a dry October night in 1889, when a fire that began in a hay barn behind the carding mill spread quickly through the wooden buildings along Main Street. By the time the flames were brought under control, twenty-six buildings had burned, including the schoolhouse and both mills. The town council responded within weeks by passing an ordinance requiring that any new construction along Main Street use brick or stone rather than wood. The rebuilt town center, much of which still stands today, gave Millbrook the solid, uniform brick storefronts that later became its best-known feature.",

  "The Millbrook clocktower, the structure most associated with the town today, was not part of the original settlement at all. It was completed in 1902, funded largely by a donation from Louisa Carter Whitfield, Edmund Carter's granddaughter, who had married into a shipping family in Boston and wished to honor her grandfather's memory. The clock mechanism itself was imported from a workshop in Switzerland and shipped by rail from New York. The tower stands eighty-two feet tall and was, for several decades, the tallest structure between the county seat and the state capital. Its bell still rings on the hour, a fact that longtime residents mention to visitors with evident pride.",

  "Millbrook's population today stands at roughly four thousand two hundred residents, and while the original mills no longer operate, the town has kept much of its nineteenth-century character. Each year, on the first weekend of September, the town holds the Millrace Festival, a two-day event celebrating Millbrook's milling heritage. The festival's best-attended event is a pie-baking contest judged by descendants of the town's founding families, a tradition that began, according to festival records, in 1951.",

  "The Millbrook Historical Society maintains a small museum inside the original millhouse, the only one of Carter's mill buildings to survive the 1889 fire largely intact. The museum displays tools from the original gristmill, back issues of the Millbrook Courier, and a scale model of the town as it looked in 1900. It is open to the public on Wednesdays and Saturdays, and admission is free, though donations are welcomed to support the building's ongoing restoration.",

  "Despite its small size, Millbrook draws several thousand visitors during festival weekend, more than the town's own year-round population. Local guides point out that the brick storefronts rebuilt after the 1889 fire, the scale model of 1900s Millbrook inside the historical society museum, and the sound of the clocktower bell on the hour together give visitors a fairly complete picture of how a nineteenth-century mill town could grow, burn, and rebuild itself within a single lifetime."
];
