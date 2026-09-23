# Quick Practice verification

The bot creates a Practice session in process memory. Each new session and question gives its answer and Next buttons a distinct callback identity. Repeated or delayed taps from an earlier question or session are ignored. Completed results are recorded in Supabase. A process restart interrupts an unfinished quiz, so start this check only after deployment is healthy.

## Automated checks

Run from the repository root with the dependencies in `requirements.txt` installed:

```powershell
python -m unittest tests.test_quiz tests.test_practice_callbacks -v
python -m unittest discover -s tests -q
```

`tests.test_quiz` covers malformed model output, one bounded retry, and rejection after a second malformed response. `tests.test_practice_callbacks` simulates three questions, duplicate taps, a delayed Question 1 tap after advancing, score recording, and buttons from an earlier session. These tests do not contact Telegram or OpenAI.

## Live student-account check

Use your own authorized Telegram account and the production BuildWise bot. Do not use a bot token as a student account. Record the date, bot identity, deployment commit, and screenshots or message times without exposing private student data.

1. Send `/progress` and note the **Practice sessions** count for the last seven days.
2. Send `/practice`. Enter `stress` as the topic, choose **Medium**, then **3 Questions**. Wait for **Question 1 / 3**. If quiz generation fails, record the exact message and time; stop this run.
3. Tap one answer on **Question 1**. Note whether the feedback is correct and shows an explanation. Tap the same answer button on the original Question 1 message again. It must not add another scored answer or end the session.
4. Tap **Next Question** beneath the feedback. Confirm **Question 2 / 3** appears.
5. Return to the original **Question 1** message and tap its answer button again. Then tap a button on **Question 2**. Question 2 must accept its own answer and show one feedback message; the old Question 1 tap must not answer it or trigger “Use /practice to start a new session.”
6. Tap **Next Question** and confirm **Question 3 / 3**. Answer it, then tap **See Results**. Confirm **PRACTICE COMPLETE** reports a score out of three, with mistakes matching the answers you chose. There should be one completion message.
7. Send `/progress` again. **Practice sessions** should have increased by one in the seven-day window. **Quiz accuracy** aggregates other quizzes in that window, so it need not equal this run's score.
8. Repeat a new three-question `stress` run once. Note any generation failure, missing next question, duplicate feedback, lost session, or incorrect score with the message time for log correlation.

Deployment health and passing automated tests do not establish that this live flow passed. If a run fails, retain the message time and exact step so production logs can be checked without sharing credentials.
