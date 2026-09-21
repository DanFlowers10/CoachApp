import os
import calendar as cal_module
from datetime import datetime, date, timedelta

from flask import Flask, render_template, redirect, url_for, request, flash, abort
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

from models import db, User, TrainingPlan, Workout, StravaToken
import strava

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
db_url = os.environ.get("DATABASE_URL", "sqlite:///coach.db")
if db_url.startswith("postgres://"):
    # Some hosts (Render, Heroku) hand back the old-style scheme; SQLAlchemy needs the new one.
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET")
STRAVA_REDIRECT_URI = os.environ.get("STRAVA_REDIRECT_URI", "http://localhost:5000/strava/callback")

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ---------------------------------------------------------------- helpers --

def coach_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_coach():
            abort(403)
        return fn(*args, **kwargs)

    return wrapper


def client_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or current_user.is_coach():
            abort(403)
        return fn(*args, **kwargs)

    return wrapper


# -------------------------------------------------------------------- auth --

@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("coach_dashboard" if current_user.is_coach() else "client_dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    """Coach self-signup. Clients are created by their coach, not here."""
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
            return render_template("register.html")

        user = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
            role="coach",
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("Welcome! Start by adding your first athlete.", "success")
        return redirect(url_for("coach_dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for("index"))

        flash("Incorrect email or password.", "error")

    return render_template("login.html")


@app.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_password = request.form["current_password"]
        new_password = request.form["new_password"]
        confirm_password = request.form["confirm_password"]

        if not check_password_hash(current_user.password_hash, current_password):
            flash("Current password is incorrect.", "error")
        elif len(new_password) < 6:
            flash("New password must be at least 6 characters.", "error")
        elif new_password != confirm_password:
            flash("New password and confirmation don't match.", "error")
        else:
            current_user.password_hash = generate_password_hash(new_password)
            db.session.commit()
            flash("Password updated.", "success")
            return redirect(url_for("index"))

    return render_template("change_password.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ------------------------------------------------------------ coach views --

@app.route("/coach")
@login_required
@coach_required
def coach_dashboard():
    clients = User.query.filter_by(coach_id=current_user.id).order_by(User.name).all()
    return render_template("coach_dashboard.html", clients=clients)


@app.route("/coach/clients/new", methods=["GET", "POST"])
@login_required
@coach_required
def new_client():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        temp_password = request.form["temp_password"]

        if User.query.filter_by(email=email).first():
            flash("A user with that email already exists.", "error")
            return render_template("new_client.html")

        client = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(temp_password),
            role="client",
            coach_id=current_user.id,
        )
        db.session.add(client)
        db.session.commit()
        flash(f"Athlete account created. Give {email} their temporary password to log in.", "success")
        return redirect(url_for("coach_dashboard"))

    return render_template("new_client.html")


@app.route("/coach/clients/<int:client_id>")
@login_required
@coach_required
def client_detail(client_id):
    client = User.query.filter_by(id=client_id, coach_id=current_user.id).first_or_404()
    plans = TrainingPlan.query.filter_by(client_id=client.id).order_by(TrainingPlan.created_at.desc()).all()
    return render_template("client_detail.html", client=client, plans=plans)


@app.route("/coach/clients/<int:client_id>/plans/new", methods=["GET", "POST"])
@login_required
@coach_required
def new_plan(client_id):
    client = User.query.filter_by(id=client_id, coach_id=current_user.id).first_or_404()

    if request.method == "POST":
        plan = TrainingPlan(
            client_id=client.id,
            coach_id=current_user.id,
            title=request.form["title"].strip(),
            goal_race=request.form.get("goal_race", "").strip(),
            notes=request.form.get("notes", "").strip(),
        )
        db.session.add(plan)
        db.session.commit()
        flash("Plan created. Now add some workouts to it.", "success")
        return redirect(url_for("plan_detail", plan_id=plan.id))

    return render_template("new_plan.html", client=client)


def _week_mileage(wk_workouts):
    """(planned_mi, actual_mi) for a set of workouts - actual is None until something's done."""
    planned_mi = sum(w.target_distance_km for w in wk_workouts if w.target_distance_km)
    done = [w for w in wk_workouts if w.completed]
    actual_mi = round(sum(w.actual_distance_km for w in done if w.actual_distance_km), 1) if done else None
    return round(planned_mi, 1), actual_mi


def _plan_weeks(workouts):
    """Group workouts (already ordered by date) into Mon-Sun buckets relative to the plan's first workout."""
    weeks = []
    if not workouts:
        return weeks
    start = workouts[0].date
    buckets = {}
    for w in workouts:
        idx = (w.date - start).days // 7
        buckets.setdefault(idx, []).append(w)
    for idx in range(max(buckets.keys()) + 1):
        wk_workouts = buckets.get(idx, [])
        planned_mi, actual_mi = _week_mileage(wk_workouts)
        weeks.append({
            "index": idx + 1,
            "workouts": wk_workouts,
            "start": start + timedelta(days=idx * 7),
            "end": start + timedelta(days=idx * 7 + 6),
            "done": sum(1 for w in wk_workouts if w.completed),
            "total": len(wk_workouts),
            "planned_mi": planned_mi,
            "actual_mi": actual_mi,
        })
    return weeks


def _plan_months(workouts):
    """One Mon-Sun grid per calendar month the plan spans, using stdlib calendar for correct padding."""
    months = []
    if not workouts:
        return months
    workouts_by_date = {w.date: w for w in workouts}
    y, m = workouts[0].date.year, workouts[0].date.month
    end_y, end_m = workouts[-1].date.year, workouts[-1].date.month
    grid = cal_module.Calendar(firstweekday=0)
    while (y, m) <= (end_y, end_m):
        month_weeks = []
        for wk_dates in grid.monthdatescalendar(y, m):
            days = [
                {
                    "date": d,
                    "in_month": d.month == m,
                    "workout": workouts_by_date.get(d),
                }
                for d in wk_dates
            ]
            wk_workouts = [c["workout"] for c in days if c["workout"] is not None]
            planned_mi, actual_mi = _week_mileage(wk_workouts)
            month_weeks.append({"days": days, "planned_mi": planned_mi, "actual_mi": actual_mi})
        months.append({"label": date(y, m, 1).strftime("%B %Y"), "weeks": month_weeks})
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def _sync_strava_completions(workouts, strava_token):
    """Match same-day Strava runs to not-yet-done workouts and auto-complete them with the
    real distance/time. Returns an error string, if any."""
    if not workouts:
        return None
    try:
        access_token = strava.get_valid_access_token(
            strava_token, STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, db
        )
        plan_start_epoch = int(datetime.combine(workouts[0].date, datetime.min.time()).timestamp())
        activities = strava.fetch_activities_since(access_token, plan_start_epoch)

        runs_by_date = {}
        for a in activities:
            if a.get("type") not in ("Run", "TrailRun"):
                continue
            d = date.fromisoformat(a["start_date_local"][:10])
            if d not in runs_by_date or a["distance"] > runs_by_date[d]["distance"]:
                runs_by_date[d] = a

        changed = False
        for w in workouts:
            if w.completed:
                continue
            activity = runs_by_date.get(w.date)
            if not activity:
                continue
            was_rest = w.workout_type == "Rest"
            w.completed = True
            w.actual_distance_km = round(activity["distance"] / 1609.34, 1)
            w.actual_duration_min = round(activity["moving_time"] / 60)
            w.strava_activity_id = str(activity["id"])
            if was_rest:
                # They ran on a planned rest day - log it as a real (if unplanned) session
                # rather than silently ignoring it, so the plan reflects what actually happened.
                w.workout_type = "Easy Run"
                w.description = "Unplanned run logged from Strava (originally a rest day)."
            changed = True
        if changed:
            db.session.commit()
        return None
    except Exception:
        return "Couldn't sync Strava activities right now."


@app.route("/plan/<int:plan_id>")
@login_required
def plan_detail(plan_id):
    plan = db.session.get(TrainingPlan, plan_id)
    if plan is None:
        abort(404)

    # Access control: the plan's coach, or the plan's own client, may view it.
    if current_user.is_coach():
        if plan.coach_id != current_user.id:
            abort(403)
    else:
        if plan.client_id != current_user.id:
            abort(403)

    today = date.today()
    workouts = plan.workouts  # ordered by date

    strava_error = None
    if not current_user.is_coach() and current_user.strava_token:
        strava_error = _sync_strava_completions(workouts, current_user.strava_token)

    weeks = _plan_weeks(workouts)
    months = _plan_months(workouts)

    total_workouts = len(workouts)
    done_workouts = sum(1 for w in workouts if w.completed)
    percent_complete = round(done_workouts / total_workouts * 100) if total_workouts else 0
    total_km = sum(w.target_distance_km for w in workouts if w.target_distance_km)

    current_week_index = weeks[-1]["index"] if weeks else None
    for wk in weeks:
        if wk["end"] >= today and wk["done"] < wk["total"]:
            current_week_index = wk["index"]
            break

    current_month_label = None
    current_week = next((wk for wk in weeks if wk["index"] == current_week_index), None)
    if current_week:
        current_month_label = date(current_week["start"].year, current_week["start"].month, 1).strftime("%B %Y")
    current_month = next((m for m in months if m["label"] == current_month_label), months[0] if months else None)

    return render_template(
        "plan_detail.html",
        plan=plan,
        today=today,
        weeks=weeks,
        current_month=current_month,
        total_workouts=total_workouts,
        done_workouts=done_workouts,
        percent_complete=percent_complete,
        total_km=total_km,
        current_week_index=current_week_index,
        strava_error=strava_error,
    )


@app.route("/plan/<int:plan_id>/calendar")
@login_required
def plan_calendar(plan_id):
    plan = db.session.get(TrainingPlan, plan_id)
    if plan is None:
        abort(404)

    if current_user.is_coach():
        if plan.coach_id != current_user.id:
            abort(403)
    else:
        if plan.client_id != current_user.id:
            abort(403)

    today = date.today()
    workouts = plan.workouts

    if not current_user.is_coach() and current_user.strava_token:
        _sync_strava_completions(workouts, current_user.strava_token)

    months = _plan_months(workouts)

    return render_template("plan_calendar.html", plan=plan, months=months, today=today)


@app.route("/plan/<int:plan_id>/workouts/new", methods=["POST"])
@login_required
@coach_required
def new_workout(plan_id):
    plan = db.session.get(TrainingPlan, plan_id)
    if plan is None or plan.coach_id != current_user.id:
        abort(404)

    workout = Workout(
        plan_id=plan.id,
        date=datetime.strptime(request.form["date"], "%Y-%m-%d").date(),
        workout_type=request.form["workout_type"],
        target_distance_km=request.form.get("target_distance_km") or None,
        target_duration_min=request.form.get("target_duration_min") or None,
        description=request.form.get("description", "").strip(),
    )
    db.session.add(workout)
    db.session.commit()
    return redirect(url_for("plan_detail", plan_id=plan.id))


@app.route("/plan/<int:plan_id>/workouts/bulk", methods=["GET", "POST"])
@login_required
@coach_required
def bulk_add_workouts(plan_id):
    plan = db.session.get(TrainingPlan, plan_id)
    if plan is None or plan.coach_id != current_user.id:
        abort(404)

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    if request.method == "POST":
        start_date = datetime.strptime(request.form["start_date"], "%Y-%m-%d").date()
        num_weeks = int(request.form["num_weeks"])
        created = 0

        for week in range(num_weeks):
            for i in range(7):
                workout_type = request.form.get(f"type_{i}")
                if not workout_type or workout_type == "None":
                    continue

                w = Workout(
                    plan_id=plan.id,
                    date=start_date + timedelta(days=week * 7 + i),
                    workout_type=workout_type,
                    target_distance_km=request.form.get(f"distance_{i}") or None,
                    target_duration_min=request.form.get(f"duration_{i}") or None,
                    description=request.form.get(f"description_{i}", "").strip(),
                )
                db.session.add(w)
                created += 1

        db.session.commit()
        flash(f"Added {created} workouts across {num_weeks} week(s).", "success")
        return redirect(url_for("plan_detail", plan_id=plan.id))

    return render_template("bulk_add.html", plan=plan, days=days)


@app.route("/workout/<int:workout_id>/complete", methods=["POST"])
@login_required
@client_required
def complete_workout(workout_id):
    workout = db.session.get(Workout, workout_id)
    if workout is None or workout.plan.client_id != current_user.id:
        abort(404)

    workout.completed = True
    workout.actual_distance_km = request.form.get("actual_distance_km") or workout.target_distance_km
    workout.actual_duration_min = request.form.get("actual_duration_min") or workout.target_duration_min
    db.session.commit()
    return redirect(url_for("plan_detail", plan_id=workout.plan_id))


@app.route("/workout/<int:workout_id>/delete", methods=["POST"])
@login_required
@coach_required
def delete_workout(workout_id):
    workout = db.session.get(Workout, workout_id)
    if workout is None or workout.plan.coach_id != current_user.id:
        abort(404)
    plan_id = workout.plan_id
    db.session.delete(workout)
    db.session.commit()
    return redirect(url_for("plan_detail", plan_id=plan_id))


@app.route("/plan/<int:plan_id>/delete", methods=["POST"])
@login_required
@coach_required
def delete_plan(plan_id):
    plan = db.session.get(TrainingPlan, plan_id)
    if plan is None or plan.coach_id != current_user.id:
        abort(404)
    client_id = plan.client_id
    db.session.delete(plan)
    db.session.commit()
    flash(f'Deleted "{plan.title}".', "success")
    return redirect(url_for("client_detail", client_id=client_id))


# One-time import of Connor's real Runna plan (PDF weeks 12-22 -> app weeks 1-11).
# Each week is 7 days Mon-Sun: (workout_type, distance_mi_or_None, description).
# Pace bands for a sub-3:20 marathon (goal MP ~7:35-7:40/mi):
#   easy ~8:40-9:05/mi, long-run steady ~8:25-8:55/mi, tempo/threshold ~7:00-7:20/mi,
#   VO2/short-interval ~6:40-7:00/mi.
_CONNOR_REAL_PLAN_WEEKS = [
    [("Rest", None, "Full rest, or light stretching/mobility."),
     ("Intervals", 3.2, "Mile Repeats — @ ~6:55-7:05/mi w/ 2-3min jog recovery."),
     ("Easy Run", 3.25, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Tempo", 4, "Over and Unders 1km — alternating 1km @ ~7:35/mi (MP) / 1km @ ~7:05/mi (threshold)."),
     ("Rest", None, "Full rest, or light stretching/mobility."),
     ("Easy Run", 5.5, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Long Run", 7, "Steady, easy effort throughout. ~8:25-8:55/mi.")],
    [("Rest", None, "Full rest, or light stretching/mobility."),
     ("Tempo", 6, "Progression Run — build steadily from ~8:30/mi to ~7:10/mi by the final mile."),
     ("Easy Run", 5.5, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Intervals", 4.5, "600m Madness — 600m reps @ ~6:45-6:55/mi w/ short jog recovery."),
     ("Easy Run", 4, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Easy Run", 3.25, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Long Run", 16, "Race Practice Long Run — steady ~8:25-8:55/mi, last 3mi @ goal race pace ~7:35-7:40/mi.")],
    [("Rest", None, "Full rest, or light stretching/mobility."),
     ("Tempo", 6, "Tempo 4 Miles — 1mi easy WU, 4mi @ ~7:10-7:20/mi threshold, 1mi easy CD."),
     ("Easy Run", 4.5, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Intervals", 4.5, "Pyramid Intervals — 400-800-1200-800-400m @ ~6:45-7:00/mi w/ equal-distance jog recovery."),
     ("Easy Run", 4.5, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Easy Run", 3, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Long Run", 18, "Steady, easy effort throughout. ~8:25-8:55/mi.")],
    [("Rest", None, "Full rest, or light stretching/mobility."),
     ("Intervals", 6.5, "800m Repeats — @ ~6:50-7:00/mi w/ 400m jog recovery."),
     ("Easy Run", 6.5, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Tempo", 5.5, "Tempo 3 Miles — 1.5mi easy WU, 3mi @ ~7:05-7:15/mi threshold, 1mi easy CD."),
     ("Easy Run", 6.5, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Easy Run", 4, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Long Run", 14, "Race Practice Long Run — steady ~8:25-8:55/mi, last 3mi @ goal race pace ~7:35-7:40/mi.")],
    [("Rest", None, "Full rest, or light stretching/mobility."),
     ("Tempo", 6.5, "Over and Unders Miles — alternating 1mi @ ~7:35/mi (MP) / 1mi @ ~7:00/mi (threshold), x3."),
     ("Easy Run", 5, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Intervals", 5.5, "1km Repeats — @ ~6:50-7:00/mi w/ 400m jog recovery."),
     ("Easy Run", 4.5, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Easy Run", 4, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Long Run", 20, "Steady, easy effort throughout. ~8:25-8:55/mi — peak long run.")],
    [("Rest", None, "Full rest, or light stretching/mobility."),
     ("Tempo", 3.2, "Repeating Progressive Run — short reps building from ~8:00/mi to ~6:55/mi."),
     ("Easy Run", 3.25, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Intervals", 5.5, "Broken 600s — 600m @ ~6:45/mi w/ 60-90s standing recovery, repeat."),
     ("Rest", None, "Full rest, or light stretching/mobility."),
     ("Easy Run", 6, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Long Run", 9.5, "Cutback week — steady, easy effort. ~8:25-8:55/mi.")],
    [("Easy Run", 3.25, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Intervals", 6.5, "Drop Set — descending rep distance @ increasing pace, finishing ~6:45/mi."),
     ("Easy Run", 6, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Tempo", 6, "Tempo 2 Miles — 2mi easy WU, 2mi @ ~7:00-7:10/mi threshold, 2mi easy CD."),
     ("Rest", None, "Full rest, or light stretching/mobility."),
     ("Easy Run", 3.5, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Long Run", 22, "Race Practice Long Run — steady ~8:25-8:55/mi, last 4-5mi @ goal race pace ~7:35-7:40/mi — peak long run.")],
    [("Rest", None, "Full rest, or light stretching/mobility."),
     ("Intervals", 6.5, "400s into 200s — 400m @ ~6:40-6:50/mi into 200m @ ~6:20/mi, w/ short jog recovery."),
     ("Easy Run", 5, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Tempo", 6.5, "Over and Unders 400m — alternating 400m @ ~7:35/mi (MP) / 400m @ ~6:50/mi (threshold)."),
     ("Easy Run", 4.5, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Easy Run", 6.5, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Long Run", 20, "Steady, easy effort throughout. ~8:25-8:55/mi.")],
    [("Rest", None, "Full rest, or light stretching/mobility."),
     ("Tempo", 5.5, "Mile Up & Overs — alternating 1mi @ ~7:35/mi (MP) / 1mi @ ~7:00/mi (threshold)."),
     ("Easy Run", 7, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Tempo", 5.5, "Over and Unders Miles — alternating 1mi @ ~7:35/mi (MP) / 1mi @ ~7:00/mi (threshold)."),
     ("Easy Run", 5, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Easy Run", 3.75, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Long Run", 14, "Race Practice Long Run — steady ~8:25-8:55/mi, last 3mi @ goal race pace ~7:35-7:40/mi — taper begins next week.")],
    [("Easy Run", 6.5, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Rest", None, "Full rest, or light stretching/mobility."),
     ("Tempo", 5.5, "Progressive Mile Repeats — each mile ~5s/mi faster: ~7:20/mi to ~6:55/mi, w/ 2min jog recovery."),
     ("Easy Run", 4, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Tempo", 3.5, "Progression Run — build steadily from ~8:15/mi to ~7:05/mi by the final mile."),
     ("Rest", None, "Full rest, or light stretching/mobility."),
     ("Long Run", 8.5, "Taper — steady, easy effort. ~8:25-8:55/mi.")],
    [("Rest", None, "Full rest, or light stretching/mobility."),
     ("Easy Run", 3.25, "Easy, conversational effort. ~8:40-9:05/mi."),
     ("Tempo", 5, "Taper Fartlek — easy ~8:30/mi with 6-8 x 60-90s pickups @ ~6:55-7:05/mi, full recovery between."),
     ("Rest", None, "Full rest, or light stretching/mobility."),
     ("Easy Run", 5, "Short shakeout, very easy, legs fresh for race day. ~8:45-9:15/mi."),
     ("Rest", None, "Full rest — race tomorrow."),
     ("Race", 26.2, "Valencia Trinidad Alfonso Zurich Marathon — goal sub-3:20:00 (~7:35-7:40/mi).")],
]


@app.route("/plan/<int:plan_id>/import-connor-real-plan", methods=["POST"])
@login_required
@coach_required
def import_connor_real_plan(plan_id):
    if plan_id != 2:
        abort(404)
    plan = db.session.get(TrainingPlan, plan_id)
    if plan is None or plan.coach_id != current_user.id:
        abort(404)

    for workout in list(plan.workouts):
        db.session.delete(workout)

    start = date(2026, 9, 21)
    day_index = 0
    for week in _CONNOR_REAL_PLAN_WEEKS:
        for workout_type, distance_mi, description in week:
            db.session.add(Workout(
                plan_id=plan.id,
                date=start + timedelta(days=day_index),
                workout_type=workout_type,
                target_distance_km=distance_mi,
                target_duration_min=None,
                description=description,
            ))
            day_index += 1

    db.session.commit()
    flash("Imported Connor's real plan from the Runna PDF (weeks 1-11).", "success")
    return redirect(url_for("plan_detail", plan_id=plan.id))


# ----------------------------------------------------------- client views --

@app.route("/athlete")
@login_required
@client_required
def client_dashboard():
    plans = TrainingPlan.query.filter_by(client_id=current_user.id).order_by(TrainingPlan.created_at.desc()).all()
    return render_template("client_dashboard.html", plans=plans)


# --------------------------------------------------------- Strava OAuth ---

@app.route("/strava/connect")
@login_required
def strava_connect():
    if not STRAVA_CLIENT_ID:
        flash("Strava isn't configured yet — add STRAVA_CLIENT_ID / SECRET to your .env file.", "error")
        return redirect(request.referrer or url_for("index"))

    url = strava.get_authorize_url(STRAVA_CLIENT_ID, STRAVA_REDIRECT_URI, state=str(current_user.id))
    return redirect(url)


@app.route("/strava/callback")
@login_required
def strava_callback():
    error = request.args.get("error")
    if error:
        flash("Strava connection was cancelled.", "error")
        return redirect(url_for("index"))

    code = request.args.get("code")
    data = strava.exchange_code_for_token(STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, code)

    token_row = current_user.strava_token
    if token_row is None:
        token_row = StravaToken(user_id=current_user.id)
        db.session.add(token_row)

    token_row.access_token = data["access_token"]
    token_row.refresh_token = data["refresh_token"]
    token_row.expires_at = data["expires_at"]
    token_row.athlete_id = str(data.get("athlete", {}).get("id", ""))
    db.session.commit()

    flash("Strava connected!", "success")
    return redirect(url_for("index"))


@app.route("/strava/disconnect", methods=["POST"])
@login_required
def strava_disconnect():
    token_row = current_user.strava_token
    if token_row is not None:
        db.session.delete(token_row)
        db.session.commit()
        flash("Strava disconnected.", "success")
    return redirect(request.referrer or url_for("index"))


@app.route("/strava/activities")
@login_required
def strava_activities():
    token_row = current_user.strava_token
    if token_row is None:
        flash("Connect Strava first.", "error")
        return redirect(url_for("index"))

    access_token = strava.get_valid_access_token(token_row, STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, db)
    activities = strava.fetch_recent_activities(access_token)
    return render_template("strava_activities.html", activities=activities)


def _build_strava_stats(runs):
    """Fun/motivational rollup of a client's recent runs - streaks, weekly trend, personal bests."""
    if not runs:
        return None

    total_mi = sum(r["distance"] for r in runs) / 1609.34
    total_sec = sum(r["moving_time"] for r in runs)
    avg_pace = (total_sec / 60) / total_mi if total_mi else None  # min per mile
    longest_mi = max(r["distance"] for r in runs) / 1609.34

    run_dates_set = {date.fromisoformat(r["start_date_local"][:10]) for r in runs}
    run_dates = sorted(run_dates_set)

    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)

    this_week_mi = sum(
        r["distance"] for r in runs
        if date.fromisoformat(r["start_date_local"][:10]) >= this_monday
    ) / 1609.34
    last_week_mi = sum(
        r["distance"] for r in runs
        if last_monday <= date.fromisoformat(r["start_date_local"][:10]) < this_monday
    ) / 1609.34

    streak_days = 0
    if run_dates and (today - run_dates[-1]).days <= 1:
        cursor = run_dates[-1]
        while cursor in run_dates_set:
            streak_days += 1
            cursor -= timedelta(days=1)

    weekly_bars = []
    for i in range(7, -1, -1):
        wk_start = this_monday - timedelta(days=7 * i)
        wk_end = wk_start + timedelta(days=6)
        mi = sum(
            r["distance"] for r in runs
            if wk_start <= date.fromisoformat(r["start_date_local"][:10]) <= wk_end
        ) / 1609.34
        weekly_bars.append({"label": wk_start.strftime("%d %b"), "mi": round(mi, 1)})
    max_weekly_mi = max((b["mi"] for b in weekly_bars), default=0)

    avg_pace_str = None
    if avg_pace:
        avg_pace_str = f"{int(avg_pace)}:{round((avg_pace % 1) * 60):02d}"

    return {
        "total_runs": len(runs),
        "total_mi": round(total_mi, 1),
        "total_hours": round(total_sec / 3600, 1),
        "avg_pace_str": avg_pace_str,
        "longest_mi": round(longest_mi, 1),
        "this_week_mi": round(this_week_mi, 1),
        "last_week_mi": round(last_week_mi, 1),
        "week_trend_mi": round(this_week_mi - last_week_mi, 1),
        "streak_days": streak_days,
        "weekly_bars": weekly_bars,
        "max_weekly_mi": max_weekly_mi,
        "marathons_equivalent": round(total_mi / 26.2, 1),
    }


@app.route("/strava/overview")
@login_required
@client_required
def strava_overview():
    token_row = current_user.strava_token
    if token_row is None:
        flash("Connect Strava first.", "error")
        return redirect(url_for("index"))

    stats = None
    error = None
    try:
        access_token = strava.get_valid_access_token(token_row, STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, db)
        since_epoch = int(datetime.combine(date.today() - timedelta(days=90), datetime.min.time()).timestamp())
        activities = strava.fetch_activities_since(access_token, since_epoch)
        runs = [a for a in activities if a.get("type") in ("Run", "TrailRun")]
        stats = _build_strava_stats(runs)
    except Exception:
        error = "Couldn't load your Strava stats right now."

    return render_template("strava_overview.html", stats=stats, error=error)


# ------------------------------------------------------------------- misc --

@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, message="You don't have access to that."), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Not found."), 404


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    app.run(debug=debug_mode, port=int(os.environ.get("PORT", 5000)), host="0.0.0.0")
