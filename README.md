# Marathon Coach — prototype

A minimal coach/client training-plan app:

- Coaches sign up, add clients, build training plans, and add workouts to those plans.
- Clients log in (with credentials their coach gives them), see their plan, and mark workouts done.
- Clients can connect Strava and see their recent activities.
- Garmin is stubbed out (see "Garmin" section below — it needs an approved developer account, which I couldn't build in for you today).

## 1. Run it locally

```bash
cd marathon-coach
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # then edit .env — at minimum change SECRET_KEY
python app.py
```

Open http://localhost:5000 — it creates `coach.db` (SQLite) automatically on first run.

## 2. Try it out

1. Go to `/register` and create a coach account.
2. From the coach dashboard, click **+ Add client** — give them a name, email, and temporary password.
3. Click into that client, **+ New plan**, then add a few workouts (date, type, distance).
4. Log out, log back in as the client (using the email/password you just created) — you'll land on their plan.
5. Mark a workout "done" as the client.

## 3. Strava setup (optional)

1. Create an API app at https://www.strava.com/settings/api.
   - "Authorization Callback Domain" = `localhost`
2. Copy the Client ID and Client Secret into your `.env` file.
3. Restart the app. On the client dashboard (or a plan page), click **Connect Strava**.
4. After authorizing, you'll be redirected back and can view recent activities.

Strava's API is fully self-serve, so this part is a real, working integration — not a mock.

## 4. Garmin — important caveat

Garmin does **not** offer a self-serve OAuth app registration like Strava does. Their
Garmin Connect Developer Program requires applying and being approved by Garmin, and
terms/availability change over time. Because of that, I couldn't wire up a real, working
Garmin integration in this prototype — it would fail at "sign up for API access" before
any code could run.

What I've done instead: the data model (`Workout.strava_activity_id`, `StravaToken`) is
generic enough that a `garmin.py` module mirroring `strava.py` (same shape: authorize URL,
token exchange, fetch activities) would drop in cleanly once/if you get Garmin API access.
If Garmin access doesn't come through, a pragmatic fallback is `python-garminconnect`
(an unofficial library that logs in with the athlete's own Garmin credentials) — worth
researching the ToS implications before relying on it for real users.

## 5. What I'd extend first, in order

1. **Password reset / change-password flow.** Right now coaches set a client's initial
   password and there's no way for the client to change it. This is the biggest real gap.
2. **Recurring/templated plans.** Right now every workout is added one at a time. A "generate
   a 16-week plan from a template" feature (e.g. pick a plan type + long-run day) would save
   coaches a lot of clicking.
3. **Auto-matching Strava activities to planned workouts** by date + rough distance, instead
   of just listing them separately. This is the natural next step once the two exist side by side.
4. **Coach-facing overview** — right now a coach has to click into each client to see progress.
   A dashboard showing "who missed their last workout" across all clients is high value.
5. **Move to Postgres + a proper migration tool (Alembic)** once you're past prototyping —
   SQLite is fine for one coach testing this, not for production with concurrent writers.
6. **API layer / separate frontend.** If you want a mobile app for clients later, extract the
   routes in `app.py` into a JSON API (Flask already gives you most of this) and build a
   separate frontend against it, keeping the server-rendered pages for the coach side if you like.

## Project structure

```
app.py              - routes / app entrypoint
models.py           - SQLAlchemy models (User, TrainingPlan, Workout, StravaToken)
strava.py           - Strava OAuth + API helper functions
templates/          - Jinja2 templates (Bootstrap for styling)
static/style.css    - small custom styles
.env.example        - copy to .env and fill in secrets
```
