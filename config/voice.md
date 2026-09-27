# Voice

How every reader-facing sentence on the dashboard is written.
Keep it simple.
Nobody is here to be impressed by the language; the reader has to understand it.

## The reader

A smart friend who is not in finance.
They read carefully, they are not in a hurry, and they do not know trader shorthand.
Write for them, not for a trading desk.
Do not talk down to them either: explain a term once, then trust them with it.

## Rules

1. **Answer first.**
   Lead with what happened or what to do.
   Reasons come after.
2. **Short sentences.**
   Aim for about 15 words on average.
   Never go over 25.
   Mix short and long, but split any sentence that needs a second breath.
3. **Plain words.**
   Use the short common word: *use* not *utilise*, *before* not *prior to*, *because* not *due to the fact that*.
   Use the jargon table below.
   A reading level of grade 8 or under is the target.
4. **Terms that must stay get explained.**
   VIX, Fed, PCE, CPI, S&P 500, basis points and the like can stay.
   On first use, explain in a few words ("the VIX, a gauge of expected stock swings"), or leave the term for the page's glossary, which already explains the series names.
5. **Active voice.**
   Name who did it: "the Fed raised rates", not "rates were raised".
6. **Keep every number exact.**
   Every number, date, level and threshold stays exactly as written: 2.40 stays 2.40, Oct 6-8 stays Oct 6-8, 5.52% stays 5.52%.
7. **Keep every condition word exact.**
   *only, unless, at least, at or above, at or under, before, after, both, either, and, or* each change what a sentence means.
   Never drop or soften one.
   You may move the condition to the front, but it keeps its word: "only once X, do Y" becomes "do Y, but only once X" or "only once X, do Y", never "once X, do Y".
8. **One name per thing.**
   Pick one plain name and keep it for the whole text.
   Do not call the same thing three different names for variety.
9. **Titles read like headlines.**
   Under about 10 words.
   Say what happens, in plain words: "Bonds sell off and 5% yields finally hurt stocks".
10. **No em dashes.**
    Not one.
    Use a comma, a colon, or a new sentence.
11. **No filler.**
    Cut words that add nothing.
    No preamble ("It is worth noting that"), no summary closer, no stacked hedges ("could potentially"), no padded lists of three.
    Skip the usual AI words: *delve, navigate, leverage, landscape, robust, crucial, pivotal, underscore, nuanced*.

## Conditions and triggers

A trigger clause or a "holds while" condition is checked by a machine against a number.
Lead with plain words, then give the measurable part exactly as the expression checks it.

- Before: "The Oct 6-8 3Y/10Y/30Y cluster clears without a demand failure: the 10Y bid-to-cover at or above 2.40 and the 30Y stopping at or under 5.52%"
- After: "The Oct 6-8 Treasury auctions go well. 10-year demand is at least 2.40 times the amount sold, and the 30-year yield is 5.52% or less"

The number, the comparison and the series are unchanged.
Only the words around them got plainer.

## Jargon table

The checker reads the same list from `config/jargon.yaml`.
Add a term there and here together; a test fails when they differ.

| Instead of | Say |
| --- | --- |
| tape | the market, prices |
| breadth | the share of S&P 500 stocks above their 200-day average. Say how many stocks are rising, not "breadth" |
| duration | long-term bonds |
| high-beta | the most volatile stocks |
| beta | how much something swings with the market |
| transmits | reaches, starts to hurt |
| prints | comes out, is reported (a "print" is a data release) |
| leg | part, signal, holding |
| 200dma | 200-day average price |
| bid-to-cover | how many dollars were bid for each dollar of bonds sold |
| OAS | the extra yield over Treasuries |
| spreads | the extra yield riskier borrowers pay over the government. The credit sense; the verb ("spreads to") is fine |
| HY | junk bonds (high-yield) |
| IG | investment-grade (safer) company bonds |
| risk complex | stocks, junk bonds and other risky assets |
| carry | hold up, lift. As in "memory carries the index" |
| add | buy more, extra buying. The noun ("no add", "the crypto add") and "adds"; the verb in plain use is fine |
| floor of the band | the low end of the target range |
| band | range, target range |
| cluster clears | the auctions go smoothly |
| cluster | the group of auctions, the week's auctions. The auction sense ("the 3Y/10Y/30Y cluster", "the auction cluster"); "a cluster of lawmakers" is fine |
| tailing | selling at a higher yield than expected (weak demand) |
| tail | a weak auction, one that sold at a higher yield than expected. The auction sense ("a record tail", "tailed by 2bp"); not "tail risk", which the page explains, nor "the tail end" |
| SEP | the Fed's own forecasts |
| dot plot | Fed officials' rate forecasts |
| reflexive | automatic, by habit |
| capex | spending on buildings and equipment |
| dovish | leaning toward lower rates |
| hawkish | leaning toward higher rates |
| risk-on | investors buying riskier assets |
| risk-off | investors selling riskier assets |
| bid | buyers, demand. The noun ("a bid under long bonds", "no bid"); a takeover bid is fine |
| offer | sellers, supply |
| rip | sharp rise |
| chop | prices going sideways |
| squeeze | a jump forced by traders buying back bets against it |
| positioning | what to do, how to hold |
| drawdown | fall from the peak |
| internals | what most stocks are doing underneath the index |
| composition | which stocks are driving the index |
| run low | the lowest level in this stretch |
| coin flip | about even odds |
| clears | passes, rises above. A price getting past a level ("clears 76,000", "cleared resistance"); "cleared by regulators" and "clears the way" are fine |
| reclaims | climbs back to |
| front end | short-term interest rates |
| term premium | the extra yield for lending for longer |
| unwind | investors selling out of a popular trade |
| crowded | a trade too many investors already own |
| guide | the company's own forecast. The earnings sense ("raised guide", "guided up"); "a guide to" and "guidance" are fine |
| frothy | overheated |
| flush | a quick, sharp drop |
| shelf | a price level that has held |
| 2s10s | the gap between 2-year and 10-year Treasury yields |
| complex | group. The noun, as in "the AI complex" |

## Before and after

From the W39 week, rewritten in this voice.

**Weekly summary, opening.**
Before: "The Fed hiked into a 5% ten-year and a closed Hormuz, and the risk complex answered by sending the VIX to a run low and tightening investment-grade spreads, which is the week's real information: nothing that prices risk is treating the macro as a risk."
After: "Markets are shrugging off the risks. The Fed raised rates with the ten-year Treasury yield at 5% and the Strait of Hormuz closed. Yet the VIX, a gauge of expected stock swings, fell to its lowest level in this stretch."

**Scenario title.**
Before: "Duration fails and the 5% ten-year finally transmits"
After: "Bonds sell off and 5% yields finally hurt stocks"

**Positioning, keeping "only".**
Before: "Cut high-beta, not quality, and only once a confirming leg prints; hold cash for a retest of the rising 200dma; do not use gold or crypto as the equity hedge"
After: "Sell the most volatile stocks, not the quality ones, but only once a second warning sign appears. Keep cash to buy if the S&P 500 falls back to its rising 200-day average. Do not use gold or crypto to protect the stock holdings."

**Positioning, keeping "only after".**
Before: "Add toward the middle of the equity band only after breadth reclaims 55%; the crypto add is gated on the inflow week, not on price"
After: "Buy more stocks, up to the middle of the target range, but only after 55% of S&P 500 stocks are back above their 200-day average. Buy more crypto only after a week when money flows into crypto funds. Do not buy on price alone."

**Trigger clause.**
Before: "The Sep 22-24 cluster clears without a second record tail"
After: "The Sep 22-24 Treasury auctions go smoothly, with no second record-weak sale"

**Phase reason.**
Before: "small caps took over leadership as credit compressed and the equal-weight index outran the cap-weight"
After: "More kinds of stocks are now pulling the market up. Smaller companies are leading, and lenders are charging riskier firms less."

---

The rules above adapt the plain-english skill by b1rdmania (github.com/b1rdmania/claude-plain-english-skill), MIT licence, (c) 2025-2026 b1rdmania.
