# Example calls

Each directory is one call. All of them are written for testing: the people,
businesses and conversations are fictional, and there is no audio.

| File | What it is |
|---|---|
| `transcript.json` | speaker-labelled, timestamped transcript (`Transcript` in `sel/models.py`) |
| `analysis.json` | **hand-written stand-in for model output**; may contain deliberate mistakes to exercise the evidence gate |
| `gold.json` | what a careful human reviewer would mark; used by `python -m sel eval` |

| Call | Pack | Shows |
|---|---|---|
| `solar-01` | `demo-solar` | mostly Nepali; one clear date ("bholi beluka samma"), two vague ones ("sometime next week", "pachhi"), a customer commitment, a contradicted warranty claim, and three fixture mistakes that Sel refuses or corrects |
| `pos-01` | `demo-pos` | mostly English; a vague "next Sunday 3 baje" that the customer then settles ("Yo Sunday … diuso 3 baje"), producing a ready calendar invite |
