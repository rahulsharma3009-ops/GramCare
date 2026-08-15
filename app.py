import os, json, sqlite3, uuid, re, secrets
from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO

import requests
import qrcode
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data', 'gramcare.db')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'gramcare-demo-secret-change-me')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '').strip()
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.6-flash').strip()

ROLES = {'doctor', 'patient', 'cmo'}


def now():
    return datetime.now().isoformat(timespec='seconds')


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_db():
    conn = db()
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS phcs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        code TEXT UNIQUE NOT NULL,
        district TEXT NOT NULL,
        block TEXT,
        village TEXT,
        address TEXT,
        phone TEXT,
        latitude REAL,
        longitude REAL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        phone TEXT,
        pin_hash TEXT,
        phc_id INTEGER,
        designation TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(phc_id) REFERENCES phcs(id)
    );
    CREATE TABLE IF NOT EXISTS patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        age INTEGER,
        sex TEXT,
        dob TEXT,
        phone TEXT,
        village TEXT,
        district TEXT,
        language TEXT DEFAULT 'Hindi',
        blood_group TEXT,
        allergies TEXT,
        conditions TEXT,
        emergency_contact TEXT,
        consent INTEGER DEFAULT 1,
        user_id INTEGER,
        registered_phc_id INTEGER,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(registered_phc_id) REFERENCES phcs(id)
    );
    CREATE TABLE IF NOT EXISTS encounters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        provider_id INTEGER,
        phc_id INTEGER,
        symptoms TEXT,
        duration TEXT,
        temperature TEXT,
        bp TEXT,
        spo2 TEXT,
        pulse TEXT,
        pain TEXT,
        notes TEXT,
        doctor_notes TEXT,
        prescription TEXT,
        followup_plan TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE,
        FOREIGN KEY(provider_id) REFERENCES users(id),
        FOREIGN KEY(phc_id) REFERENCES phcs(id)
    );
    CREATE TABLE IF NOT EXISTS assessments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        encounter_id INTEGER NOT NULL,
        patient_id INTEGER NOT NULL,
        risk_level TEXT NOT NULL,
        confidence TEXT,
        summary TEXT,
        red_flags TEXT,
        first_aid TEXT,
        next_steps TEXT,
        action TEXT,
        rationale TEXT,
        differential TEXT,
        possible_causes TEXT,
        suggested_tests TEXT,
        ai_used INTEGER DEFAULT 0,
        doctor_review TEXT DEFAULT 'Pending',
        created_at TEXT NOT NULL,
        FOREIGN KEY(encounter_id) REFERENCES encounters(id) ON DELETE CASCADE,
        FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        assessment_id INTEGER,
        from_doctor_id INTEGER,
        from_phc_id INTEGER,
        facility TEXT NOT NULL,
        reason TEXT NOT NULL,
        priority TEXT NOT NULL,
        status TEXT DEFAULT 'Pending',
        referred_hospital TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE,
        FOREIGN KEY(from_doctor_id) REFERENCES users(id),
        FOREIGN KEY(from_phc_id) REFERENCES phcs(id),
        FOREIGN KEY(assessment_id) REFERENCES assessments(id)
    );
    CREATE TABLE IF NOT EXISTS consultations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        doctor_id INTEGER NOT NULL,
        referral_id INTEGER,
        scheduled_at TEXT NOT NULL,
        duration INTEGER DEFAULT 30,
        room_code TEXT UNIQUE NOT NULL,
        doctor_fee REAL DEFAULT 0,
        patient_fee REAL DEFAULT 0,
        platform_fee REAL DEFAULT 0,
        status TEXT DEFAULT 'Scheduled',
        notes TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE,
        FOREIGN KEY(doctor_id) REFERENCES users(id),
        FOREIGN KEY(referral_id) REFERENCES referrals(id)
    );
    CREATE TABLE IF NOT EXISTS followups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        doctor_id INTEGER,
        due_date TEXT NOT NULL,
        reason TEXT NOT NULL,
        status TEXT DEFAULT 'Due',
        notes TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE,
        FOREIGN KEY(doctor_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS medicines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        prescribed_by INTEGER,
        name TEXT NOT NULL,
        dose TEXT,
        schedule TEXT,
        duration TEXT,
        instructions TEXT,
        active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE,
        FOREIGN KEY(prescribed_by) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        uploaded_by INTEGER,
        filename TEXT NOT NULL,
        original_name TEXT NOT NULL,
        category TEXT,
        notes TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE,
        FOREIGN KEY(uploaded_by) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS admissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        phc_id INTEGER NOT NULL,
        admitted_by INTEGER NOT NULL,
        bed_number TEXT,
        reason TEXT NOT NULL,
        condition_on_admission TEXT,
        admission_date TEXT NOT NULL,
        expected_discharge TEXT,
        status TEXT DEFAULT 'Admitted',
        discharge_date TEXT,
        discharge_summary TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE,
        FOREIGN KEY(phc_id) REFERENCES phcs(id),
        FOREIGN KEY(admitted_by) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS inpatient_updates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admission_id INTEGER NOT NULL,
        doctor_id INTEGER NOT NULL,
        update_date TEXT NOT NULL,
        condition TEXT,
        vitals TEXT,
        medications TEXT,
        clinical_notes TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(admission_id) REFERENCES admissions(id) ON DELETE CASCADE,
        FOREIGN KEY(doctor_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS notices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cmo_id INTEGER NOT NULL,
        phc_id INTEGER,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        priority TEXT DEFAULT 'Normal',
        created_at TEXT NOT NULL,
        expires_at TEXT,
        FOREIGN KEY(cmo_id) REFERENCES users(id),
        FOREIGN KEY(phc_id) REFERENCES phcs(id)
    );
    CREATE TABLE IF NOT EXISTS facilities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        facility_type TEXT,
        village TEXT,
        district TEXT,
        phone TEXT,
        latitude REAL,
        longitude REAL,
        services TEXT
    );
    CREATE TABLE IF NOT EXISTS emergency_cases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        location TEXT,
        reason TEXT,
        status TEXT DEFAULT 'Open',
        created_at TEXT NOT NULL,
        FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS otp_challenges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phone TEXT NOT NULL,
        otp_hash TEXT NOT NULL,
        purpose TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        attempts INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );
    ''')
    # Lightweight migrations for databases from earlier versions.
    migrations = {
        'users': [('phc_id','INTEGER'), ('designation','TEXT')],
        'patients': [('dob','TEXT'), ('district','TEXT'), ('registered_phc_id','INTEGER')],
        'encounters': [('phc_id','INTEGER'), ('doctor_notes','TEXT'), ('prescription','TEXT'), ('followup_plan','TEXT')],
        'assessments': [('differential','TEXT'), ('possible_causes','TEXT'), ('suggested_tests','TEXT')],
        'referrals': [('from_doctor_id','INTEGER'), ('from_phc_id','INTEGER'), ('referred_hospital','TEXT')],
        'consultations': [('doctor_fee','REAL DEFAULT 0'), ('patient_fee','REAL DEFAULT 0'), ('platform_fee','REAL DEFAULT 0')],
        'followups': [('doctor_id','INTEGER')],
        'reports': [('uploaded_by','INTEGER')],
    }
    for table, cols in migrations.items():
        existing = {r['name'] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()}
        for col, typ in cols:
            if col not in existing:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {typ}')

    # Seed PHCs.
    phcs = [
        ('Rampur Primary Health Centre','PHC-RAMPUR','Lucknow','Mohan','Rampur','Rampur Main Road','',26.8467,80.9462),
        ('Lakshmipur Primary Health Centre','PHC-LAKSHMI','Lucknow','Mohan','Lakshmipur','Lakshmipur Road','',26.8500,80.9600),
        ('Block Community Health Centre','CHC-BLOCK','Lucknow','Mohan','Block Centre','Block Health Campus','',26.8550,80.9700),
    ]
    for x in phcs:
        if not conn.execute('SELECT 1 FROM phcs WHERE code=?',(x[1],)).fetchone():
            conn.execute('INSERT INTO phcs(name,code,district,block,village,address,phone,latitude,longitude,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(*x,now()))
    conn.commit()
    phc1 = conn.execute("SELECT id FROM phcs WHERE code='PHC-RAMPUR'").fetchone()['id']
    phc2 = conn.execute("SELECT id FROM phcs WHERE code='PHC-LAKSHMI'").fetchone()['id']

    # Seed users: two PHC doctors, one CMO. No worker login is created.
    seed_users = [
        ('Dr. Asha Verma','asha.verma@gramcare.gov.in','doctor123','doctor','+919876500001',phc1,'Medical Officer'),
        ('Dr. Rajesh Kumar','rajesh.kumar@gramcare.gov.in','doctor123','doctor','+919876500002',phc2,'Medical Officer'),
        ('Dr. Neha Singh','neha.singh@gramcare.gov.in','doctor123','doctor','+919876500003',phc1,'Senior Medical Officer'),
        ('Chief Medical Officer','cmo@gramcare.gov.in','cmo123','cmo','+919876500099',None,'Chief Medical Officer'),
    ]
    for name,email,pw,role,phone,pid,designation in seed_users:
        if not conn.execute('SELECT 1 FROM users WHERE email=?',(email,)).fetchone():
            conn.execute('INSERT INTO users(name,email,password_hash,role,phone,phc_id,designation,created_at) VALUES(?,?,?,?,?,?,?,?)',
                         (name,email,generate_password_hash(pw),role,phone,pid,designation,now()))
    # Seed patient user with secure PIN.
    patient_email='patient@gramcare.local'
    if not conn.execute('SELECT 1 FROM users WHERE email=?',(patient_email,)).fetchone():
        conn.execute('INSERT INTO users(name,email,password_hash,role,phone,pin_hash,created_at) VALUES(?,?,?,?,?,?,?)',
                     ('Sita Devi',patient_email,generate_password_hash('patient123'),'patient','+919876543210',generate_password_hash('1234'),now()))
    pu = conn.execute('SELECT id FROM users WHERE email=?',(patient_email,)).fetchone()['id']
    if not conn.execute('SELECT 1 FROM patients').fetchone():
        conn.execute('INSERT INTO patients(code,name,age,sex,phone,village,district,language,blood_group,allergies,conditions,emergency_contact,consent,user_id,registered_phc_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     ('GC-26-A1B2C3','Sita Devi',42,'Female','+919876543210','Rampur','Lucknow','Hindi','B+','None known','Hypertension','+919123456789',1,pu,phc1,now()))
    else:
        conn.execute('UPDATE patients SET user_id=?, registered_phc_id=COALESCE(registered_phc_id,?) WHERE name=? AND user_id IS NULL',(pu,phc1,'Sita Devi'))
    # Facilities.
    if conn.execute('SELECT COUNT(*) c FROM facilities').fetchone()['c']==0:
        facilities=[
            ('Rampur Primary Health Centre','PHC','Rampur','Lucknow','',26.8467,80.9462,'OPD, maternal care, basic tests, pharmacy'),
            ('Lakshmipur Primary Health Centre','PHC','Lakshmipur','Lucknow','',26.8500,80.9600,'OPD, chronic care, basic tests, pharmacy'),
            ('Block Community Health Centre','CHC','Block Centre','Lucknow','',26.8550,80.9700,'Emergency, diagnostics, referrals'),
            ('District Hospital Lucknow','District Hospital','District HQ','Lucknow','',26.8600,80.9800,'Specialists, imaging, emergency, surgery'),
        ]
        for x in facilities:
            conn.execute('INSERT INTO facilities(name,facility_type,village,district,phone,latitude,longitude,services) VALUES(?,?,?,?,?,?,?,?)',x)
    conn.commit(); conn.close()


def json_list(value):
    if value is None: return []
    try:
        x=json.loads(value); return x if isinstance(x,list) else [x]
    except Exception: return [str(value)]

app.jinja_env.filters['fromjson']=json_list


def login_required(fn):
    @wraps(fn)
    def wrapper(*args,**kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login', next=request.path))
        return fn(*args,**kwargs)
    return wrapper


def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args,**kwargs):
            if not session.get('user_id'):
                return redirect(url_for('login'))
            if session.get('role') not in roles:
                flash('You do not have access to this portal.','error')
                return redirect(url_for('dashboard'))
            return fn(*args,**kwargs)
        return wrapper
    return deco


def normalize_phone(value):
    raw=re.sub(r'\D','',value or '')
    if raw.startswith('91') and len(raw)==12: raw=raw[2:]
    return '+91'+raw if len(raw)==10 else ('+'+raw if raw else '')


def patient_for_user(user_id):
    conn=db(); p=conn.execute('SELECT * FROM patients WHERE user_id=?',(user_id,)).fetchone(); conn.close(); return p


def doctor_for_user(user_id):
    conn=db(); d=conn.execute('SELECT u.*,p.name phc_name,p.code phc_code FROM users u LEFT JOIN phcs p ON p.id=u.phc_id WHERE u.id=? AND u.role=\'doctor\'',(user_id,)).fetchone(); conn.close(); return d


def generate_health_id(conn):
    while True:
        code='GC-'+datetime.now().strftime('%y')+'-'+secrets.token_hex(3).upper()
        if not conn.execute('SELECT 1 FROM patients WHERE code=?',(code,)).fetchone(): return code


def create_otp(phone,purpose):
    otp=f'{secrets.randbelow(1000000):06d}'
    conn=db(); conn.execute('DELETE FROM otp_challenges WHERE phone=? AND purpose=?',(phone,purpose)); conn.execute('INSERT INTO otp_challenges(phone,otp_hash,purpose,expires_at,attempts,created_at) VALUES(?,?,?,?,?,?)',(phone,generate_password_hash(otp),purpose,(datetime.now()+timedelta(minutes=5)).isoformat(timespec='seconds'),0,now())); conn.commit(); conn.close(); return otp


def verify_otp(phone,otp,purpose):
    conn=db(); row=conn.execute('SELECT * FROM otp_challenges WHERE phone=? AND purpose=? ORDER BY id DESC LIMIT 1',(phone,purpose)).fetchone()
    if not row: conn.close(); return False,'No active OTP.'
    if datetime.fromisoformat(row['expires_at'])<datetime.now(): conn.close(); return False,'OTP expired.'
    if row['attempts']>=5: conn.close(); return False,'Too many attempts.'
    if not check_password_hash(row['otp_hash'],otp or ''):
        conn.execute('UPDATE otp_challenges SET attempts=attempts+1 WHERE id=?',(row['id'],)); conn.commit(); conn.close(); return False,'Incorrect OTP.'
    conn.execute('DELETE FROM otp_challenges WHERE id=?',(row['id'],)); conn.commit(); conn.close(); return True,'Verified.'


def safe_float(x):
    try: return float(str(x).replace('°','').strip())
    except: return None


def triage(enc):
    red=[]; risk='Low'; action='Routine monitoring'; rationale=[]
    spo2=safe_float(enc.get('spo2')); temp=safe_float(enc.get('temperature')); pulse=safe_float(enc.get('pulse')); pain=safe_float(enc.get('pain'))
    symptoms=(enc.get('symptoms') or '').lower()
    if spo2 is not None and spo2<90: red.append('Very low oxygen saturation'); risk='Emergency'; action='Immediate emergency escalation and clinician review'; rationale.append('SpO₂ below 90% is a critical reported finding.')
    elif spo2 is not None and spo2<94: red.append('Low oxygen saturation'); risk='High'; action='Urgent doctor review'; rationale.append('SpO₂ is below the configured high-risk threshold.')
    if temp is not None and temp>=103: red.append('Very high temperature'); risk='High' if risk!='Emergency' else risk; rationale.append('Markedly elevated temperature warrants clinical review.')
    elif temp is not None and temp>=100.4: rationale.append('Fever reported.')
    if pulse is not None and (pulse>=120 or pulse<50): red.append('Abnormal pulse'); risk='High' if risk!='Emergency' else risk; action='Urgent doctor review' if risk!='Emergency' else action
    for word in ['unconscious','severe breathing','chest pain','seizure','heavy bleeding','blue lips']:
        if word in symptoms: red.append('Possible emergency symptom: '+word); risk='Emergency'; action='Immediate emergency escalation and clinician review'; rationale.append('Reported symptom contains an emergency indicator.')
    if pain is not None and pain>=9: red.append('Severe pain'); risk='High' if risk!='Emergency' else risk
    if risk=='Low' and symptoms.strip(): risk='Moderate'; action='Doctor review / structured follow-up'
    if not rationale: rationale.append('No configured high-risk rule was triggered; clinician review is still required.')
    return {'risk_level':risk,'red_flags':red,'action':action,'rationale':rationale}


def call_gemini(enc,patient,rules):
    if not GEMINI_API_KEY: return None,'Gemini API key is not configured; deterministic safety engine used.'
    prompt=f'''You are a clinical decision-support assistant supporting a qualified doctor in a rural primary-health-care setting. Return ONLY valid JSON.
Do NOT claim certainty or replace a doctor. Provide a detailed differential assessment, not a definitive diagnosis. Do not invent missing facts.
JSON schema:
{{"risk_level":"Low|Moderate|High|Emergency","confidence":"Low|Medium|High","summary":"detailed clinical summary","red_flags":["..."],"first_aid":["..."],"next_steps":["..."],"action":"...","rationale":["..."],"differential":["condition possibility + why it may fit + what would make it more/less likely"],"possible_causes":["..."],"suggested_tests":["test or clinical check + purpose"]}}
Patient: {patient['name']}, age {patient.get('age')}, sex {patient.get('sex')}, conditions {patient.get('conditions') or 'not recorded'}, allergies {patient.get('allergies') or 'not recorded'}.
Encounter symptoms: {enc.get('symptoms') or 'not provided'}; duration: {enc.get('duration') or 'not provided'}; temperature: {enc.get('temperature') or 'not provided'}; BP: {enc.get('bp') or 'not provided'}; SpO2: {enc.get('spo2') or 'not provided'}; pulse: {enc.get('pulse') or 'not provided'}; pain: {enc.get('pain') or 'not provided'}; notes: {enc.get('notes') or 'not provided'}.
Safety engine: {json.dumps(rules)}
Provide enough detail to help the PHC doctor decide whether to treat locally, follow up, refer, or escalate. Mention important missing information.''' 
    url=f'https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent'
    payload={'contents':[{'role':'user','parts':[{'text':prompt}]}],'generationConfig':{'responseMimeType':'application/json','temperature':0.2,'maxOutputTokens':2600}}
    try:
        r=requests.post(url,headers={'x-goog-api-key':GEMINI_API_KEY,'Content-Type':'application/json'},json=payload,timeout=40)
        if r.status_code>=400: return None,f'Gemini API error {r.status_code}: {r.text[:300]}'
        text=r.json()['candidates'][0]['content']['parts'][0]['text'].replace('```json','').replace('```','').strip()
        data=json.loads(text)
        for k in ['red_flags','first_aid','next_steps','rationale','differential','possible_causes','suggested_tests']:
            if isinstance(data.get(k),str): data[k]=[data[k]]
            elif not isinstance(data.get(k),list): data[k]=[]
        return data,None
    except Exception as e: return None,f'Gemini request failed: {e}'


def build_assessment(enc,patient):
    rules=triage(enc); ai,err=call_gemini(enc,patient,rules)
    if ai:
        if rules['risk_level']=='Emergency': ai['risk_level']='Emergency'; ai['action']=rules['action']; ai['red_flags']=list(dict.fromkeys(rules['red_flags']+ai.get('red_flags',[])))
        ai['_ai_used']=True; ai['_message']='Gemini-assisted clinical decision support generated. Doctor review required.'; return ai
    return {'risk_level':rules['risk_level'],'confidence':'Rule-based','summary':'Automated safety assessment generated from the recorded encounter. It is not a diagnosis and requires qualified clinician review.','red_flags':rules['red_flags'] or ['No configured red flag detected'],'first_aid':['Keep the patient under appropriate observation.','Repeat vital signs if clinically indicated.','Escalate immediately if symptoms worsen.'],'next_steps':[rules['action'],'Review the complete patient record and clinical examination.'],'action':rules['action'],'rationale':rules['rationale'],'differential':['Insufficient information for a responsible differential; complete history and examination are required.'],'possible_causes':['Cause cannot be determined reliably from the recorded fields alone.'],'suggested_tests':['Order only clinically appropriate tests after examination.'],'_ai_used':False,'_message':err or 'AI unavailable; safety engine used.'}


def record_assessment(enc,patient):
    a=build_assessment(dict(enc),dict(patient)); conn=db()
    conn.execute('INSERT INTO assessments(encounter_id,patient_id,risk_level,confidence,summary,red_flags,first_aid,next_steps,action,rationale,differential,possible_causes,suggested_tests,ai_used,doctor_review,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (enc['id'],patient['id'],a['risk_level'],a.get('confidence',''),a.get('summary',''),json.dumps(a.get('red_flags',[])),json.dumps(a.get('first_aid',[])),json.dumps(a.get('next_steps',[])),a.get('action',''),json.dumps(a.get('rationale',[])),json.dumps(a.get('differential',[])),json.dumps(a.get('possible_causes',[])),json.dumps(a.get('suggested_tests',[])),1 if a.get('_ai_used') else 0,'Pending',now()))
    aid=conn.execute('SELECT last_insert_rowid()').fetchone()[0]; conn.commit(); conn.close(); return aid,a

@app.context_processor
def globals_ctx():
    conn=db(); role=session.get('role'); uid=session.get('user_id')
    user=conn.execute('SELECT u.*,p.name phc_name,p.code phc_code FROM users u LEFT JOIN phcs p ON p.id=u.phc_id WHERE u.id=?',(uid,)).fetchone() if uid else None
    if role=='doctor':
        pc=conn.execute('SELECT COUNT(*) c FROM patients WHERE registered_phc_id=?',(user['phc_id'],)).fetchone()['c'] if user and user['phc_id'] else 0
        high=conn.execute('SELECT COUNT(*) c FROM assessments a JOIN patients p ON p.id=a.patient_id WHERE p.registered_phc_id=? AND a.risk_level IN (\'High\',\'Emergency\') AND a.doctor_review=\'Pending\'',(user['phc_id'],)).fetchone()['c'] if user and user['phc_id'] else 0
    else:
        pc=conn.execute('SELECT COUNT(*) c FROM patients').fetchone()['c']; high=conn.execute("SELECT COUNT(*) c FROM assessments WHERE risk_level IN ('High','Emergency') AND doctor_review='Pending'").fetchone()['c']
    patient_code=None; patient_id=None
    if role=='patient':
        p=conn.execute('SELECT id,code FROM patients WHERE user_id=?',(uid,)).fetchone(); patient_code=p['code'] if p else None; patient_id=p['id'] if p else None
    conn.close()
    return dict(current_user=session.get('name'),current_role=role,current_user_row=dict(user) if user else None,patient_code_for_nav=patient_code,patient_id_for_nav=patient_id,patient_count=pc,high_risk=high,ai_configured=bool(GEMINI_API_KEY),gemini_model=GEMINI_MODEL)

@app.route('/')
def index(): return render_template('index.html')

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        email=request.form.get('email','').strip().lower(); pw=request.form.get('password','')
        conn=db(); u=conn.execute('SELECT * FROM users WHERE lower(email)=?',(email,)).fetchone(); conn.close()
        if u and u['role'] in ROLES and check_password_hash(u['password_hash'],pw):
            session.clear(); session.update(user_id=u['id'],name=u['name'],role=u['role'],phc_id=u['phc_id'])
            return redirect(url_for('dashboard'))
        flash('Invalid credentials or this account is not an active portal account.','error')
    return render_template('login.html')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('index'))

@app.route('/patient/register',methods=['GET','POST'])
def patient_register():
    if request.method=='POST':
        phone=normalize_phone(request.form.get('phone')); name=request.form.get('name','').strip()
        if not name or len(re.sub(r'\D','',phone))<12: flash('Enter a valid name and Indian mobile number.','error'); return render_template('patient_register.html')
        conn=db(); existing=conn.execute('SELECT * FROM patients WHERE phone=?',(phone,)).fetchone()
        if existing:
            flash('A patient record already exists for this mobile number. Please use patient login or recovery.','error'); conn.close(); return render_template('patient_register.html')
        email=f'patient.{re.sub(r"\D","",phone)}@gramcare.local'; user=conn.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()
        if not user:
            conn.execute('INSERT INTO users(name,email,password_hash,role,phone,pin_hash,created_at) VALUES(?,?,?,?,?,?,?)',(name,email,generate_password_hash(secrets.token_urlsafe(10)),'patient',phone,generate_password_hash(request.form.get('pin','1234')),now())); uid=conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        else: uid=user['id']
        code=generate_health_id(conn); phc_id=request.form.get('phc_id',type=int) or None
        conn.execute('INSERT INTO patients(code,name,age,sex,dob,phone,village,district,language,blood_group,allergies,conditions,emergency_contact,consent,user_id,registered_phc_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (code,name,request.form.get('age',type=int),request.form.get('sex'),request.form.get('dob'),phone,request.form.get('village'),request.form.get('district','Lucknow'),request.form.get('language','Hindi'),request.form.get('blood_group'),request.form.get('allergies'),request.form.get('conditions'),request.form.get('emergency_contact'),1,uid,phc_id,now()))
        conn.commit(); conn.close(); flash(f'Registration complete. Your GramCare Health ID is {code}.','success'); return redirect(url_for('patient_login'))
    conn=db(); phcs=conn.execute('SELECT * FROM phcs ORDER BY name').fetchall(); conn.close(); return render_template('patient_register.html',phcs=phcs)

@app.route('/patient/login',methods=['GET','POST'])
def patient_login():
    if request.method=='POST':
        phone=normalize_phone(request.form.get('phone')); pin=request.form.get('pin','')
        conn=db(); u=conn.execute("SELECT * FROM users WHERE role='patient' AND phone=?",(phone,)).fetchone(); conn.close()
        if u and u['pin_hash'] and check_password_hash(u['pin_hash'],pin):
            session.clear(); session.update(user_id=u['id'],name=u['name'],role='patient'); return redirect(url_for('patient_portal'))
        if u:
            otp=create_otp(phone,'login'); session['pending_phone']=phone; flash(f'Demo OTP: {otp} (valid 5 minutes).','success'); return redirect(url_for('patient_verify_otp'))
        flash('No patient account found for this mobile number.','error')
    return render_template('patient_login.html')

@app.route('/patient/verify-otp',methods=['GET','POST'])
def patient_verify_otp():
    phone=session.get('pending_phone')
    if not phone: return redirect(url_for('patient_login'))
    if request.method=='POST':
        ok,msg=verify_otp(phone,request.form.get('otp'), 'login')
        if ok:
            conn=db(); u=conn.execute("SELECT * FROM users WHERE role='patient' AND phone=?",(phone,)).fetchone(); conn.close()
            if u: session.clear(); session.update(user_id=u['id'],name=u['name'],role='patient'); return redirect(url_for('patient_portal'))
        flash(msg,'error')
    return render_template('patient_verify_otp.html',phone=phone)

@app.route('/patient/forgot-pin',methods=['GET','POST'])
def patient_forgot_pin():
    if request.method=='POST':
        phone=normalize_phone(request.form.get('phone')); conn=db(); u=conn.execute("SELECT id FROM users WHERE role='patient' AND phone=?",(phone,)).fetchone(); conn.close()
        if not u: flash('No patient account found.','error'); return render_template('patient_forgot_pin.html')
        otp=create_otp(phone,'reset'); session['reset_phone']=phone; flash(f'Demo OTP: {otp} (valid 5 minutes).','success'); return redirect(url_for('patient_reset_pin'))
    return render_template('patient_forgot_pin.html')

@app.route('/patient/reset-pin',methods=['GET','POST'])
def patient_reset_pin():
    phone=session.get('reset_phone')
    if not phone: return redirect(url_for('patient_forgot_pin'))
    if request.method=='POST':
        ok,msg=verify_otp(phone,request.form.get('otp'),'reset')
        if ok:
            conn=db(); conn.execute("UPDATE users SET pin_hash=? WHERE role='patient' AND phone=?",(generate_password_hash(request.form.get('pin','1234')),phone)); u=conn.execute("SELECT * FROM users WHERE role='patient' AND phone=?",(phone,)).fetchone(); conn.commit(); conn.close(); session.clear(); session.update(user_id=u['id'],name=u['name'],role='patient'); return redirect(url_for('patient_portal'))
        flash(msg,'error')
    return render_template('patient_reset_pin.html',phone=phone)

@app.route('/patient/health-id-login',methods=['GET','POST'])
def patient_health_id_login():
    if request.method=='POST':
        code=request.form.get('code','').strip().upper(); pin=request.form.get('pin','')
        conn=db(); p=conn.execute('SELECT * FROM patients WHERE code=?',(code,)).fetchone(); u=conn.execute('SELECT * FROM users WHERE id=?',(p['user_id'],)).fetchone() if p and p['user_id'] else None; conn.close()
        if p and u and u['pin_hash'] and check_password_hash(u['pin_hash'],pin): session.clear(); session.update(user_id=u['id'],name=u['name'],role='patient'); return redirect(url_for('patient_portal'))
        flash('Invalid Health ID or PIN.','error')
    return render_template('patient_health_id_login.html')

@app.route('/qr-login/<code>')
def qr_login(code): return redirect(url_for('patient_health_id_login',code=code))

@app.route('/health-id/<code>.png')
@login_required
def health_id_qr(code):
    img=qrcode.make(url_for('qr_login',code=code,_external=True)); buf=BytesIO(); img.save(buf,format='PNG'); buf.seek(0); return send_file(buf,mimetype='image/png')

@app.route('/dashboard')
@login_required
def dashboard():
    role=session.get('role')
    if role=='patient': return redirect(url_for('patient_portal'))
    if role=='cmo': return redirect(url_for('cmo_dashboard'))
    d=doctor_for_user(session['user_id']); conn=db(); phc_id=d['phc_id'] if d else None
    patients=conn.execute('SELECT * FROM patients WHERE registered_phc_id=? ORDER BY id DESC',(phc_id,)).fetchall()
    notices=conn.execute('SELECT n.* FROM notices n WHERE n.phc_id IS NULL OR n.phc_id=? ORDER BY n.id DESC LIMIT 5',(phc_id,)).fetchall()
    today=datetime.now().date().isoformat(); visits=conn.execute('SELECT COUNT(*) c FROM encounters WHERE phc_id=? AND substr(created_at,1,10)=?',(phc_id,today)).fetchone()['c']
    refs=conn.execute('SELECT COUNT(*) c FROM referrals WHERE from_phc_id=?',(phc_id,)).fetchone()['c']
    admitted=conn.execute("SELECT COUNT(*) c FROM admissions WHERE phc_id=? AND status='Admitted'",(phc_id,)).fetchone()['c']
    conn.close(); return render_template('doctor_dashboard.html',doctor=d,patients=patients,notices=notices,visits=visits,refs=refs,admitted=admitted)

@app.route('/patients')
@login_required
def patients():
    conn=db(); role=session.get('role')
    if role=='doctor': rows=conn.execute('SELECT p.*,ph.name phc_name FROM patients p LEFT JOIN phcs ph ON ph.id=p.registered_phc_id WHERE p.registered_phc_id=? ORDER BY p.id DESC',(session.get('phc_id'),)).fetchall()
    elif role=='cmo': rows=conn.execute('SELECT p.*,ph.name phc_name FROM patients p LEFT JOIN phcs ph ON ph.id=p.registered_phc_id ORDER BY p.id DESC').fetchall()
    else: rows=[]
    conn.close(); return render_template('patients.html',patients=rows)

@app.route('/patients/new',methods=['GET','POST'])
@role_required('doctor')
def new_patient():
    d=doctor_for_user(session['user_id'])
    if request.method=='POST':
        phone=normalize_phone(request.form.get('phone')); conn=db(); existing=conn.execute('SELECT id FROM patients WHERE phone=?',(phone,)).fetchone()
        if existing: flash('Patient already exists. Open the existing record.','error'); conn.close(); return redirect(url_for('patients'))
        code=generate_health_id(conn); email=f'patient.{re.sub(r"\D", "", phone)}@gramcare.local'; u=conn.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()
        if not u:
            conn.execute('INSERT INTO users(name,email,password_hash,role,phone,pin_hash,created_at) VALUES(?,?,?,?,?,?,?)',(request.form['name'],email,generate_password_hash(secrets.token_urlsafe(12)),'patient',phone,generate_password_hash(request.form.get('pin','1234')),now())); uid=conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        else: uid=u['id']
        conn.execute('INSERT INTO patients(code,name,age,sex,phone,village,district,language,blood_group,allergies,conditions,emergency_contact,consent,user_id,registered_phc_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(code,request.form['name'],request.form.get('age',type=int),request.form.get('sex'),phone,request.form.get('village'),request.form.get('district','Lucknow'),request.form.get('language','Hindi'),request.form.get('blood_group'),request.form.get('allergies'),request.form.get('conditions'),request.form.get('emergency_contact'),1,uid,d['phc_id'],now())); conn.commit(); conn.close(); flash(f'Patient registered with Health ID {code}.','success'); return redirect(url_for('patients'))
    return render_template('patient_new.html',doctor=d)

@app.route('/patients/<int:patient_id>')
@login_required
def patient_detail(patient_id):
    conn=db(); p=conn.execute('SELECT p.*,ph.name phc_name FROM patients p LEFT JOIN phcs ph ON ph.id=p.registered_phc_id WHERE p.id=?',(patient_id,)).fetchone()
    if not p: conn.close(); return 'Patient not found',404
    if session.get('role')=='doctor' and p['registered_phc_id']!=session.get('phc_id'): conn.close(); return 'Forbidden',403
    enc=conn.execute('SELECT e.*,u.name doctor_name,ph.name phc_name FROM encounters e LEFT JOIN users u ON u.id=e.provider_id LEFT JOIN phcs ph ON ph.id=e.phc_id WHERE e.patient_id=? ORDER BY e.id DESC',(patient_id,)).fetchall()
    refs=conn.execute('SELECT r.*,u.name doctor_name,ph.name phc_name FROM referrals r LEFT JOIN users u ON u.id=r.from_doctor_id LEFT JOIN phcs ph ON ph.id=r.from_phc_id WHERE r.patient_id=? ORDER BY r.id DESC',(patient_id,)).fetchall()
    admits=conn.execute('SELECT a.*,ph.name phc_name FROM admissions a JOIN phcs ph ON ph.id=a.phc_id WHERE a.patient_id=? ORDER BY a.id DESC',(patient_id,)).fetchall()
    meds=conn.execute('SELECT m.*,u.name doctor_name FROM medicines m LEFT JOIN users u ON u.id=m.prescribed_by WHERE m.patient_id=? ORDER BY m.id DESC',(patient_id,)).fetchall()
    consults=conn.execute('SELECT c.*,u.name doctor_name FROM consultations c JOIN users u ON u.id=c.doctor_id WHERE c.patient_id=? ORDER BY c.scheduled_at DESC',(patient_id,)).fetchall()
    conn.close(); return render_template('patient_detail.html',patient=p,encounters=enc,referrals=refs,admissions=admits,medicines=meds,consultations=consults)

@app.route('/patients/<int:patient_id>/encounter',methods=['GET','POST'])
@role_required('doctor')
def encounter(patient_id):
    d=doctor_for_user(session['user_id']); conn=db(); p=conn.execute('SELECT * FROM patients WHERE id=?',(patient_id,)).fetchone(); conn.close()
    if not p or p['registered_phc_id']!=d['phc_id']: return 'Forbidden',403
    if request.method=='POST':
        conn=db(); cur=conn.execute('INSERT INTO encounters(patient_id,provider_id,phc_id,symptoms,duration,temperature,bp,spo2,pulse,pain,notes,doctor_notes,prescription,followup_plan,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (patient_id,d['id'],d['phc_id'],request.form.get('symptoms'),request.form.get('duration'),request.form.get('temperature'),request.form.get('bp'),request.form.get('spo2'),request.form.get('pulse'),request.form.get('pain'),request.form.get('notes'),request.form.get('doctor_notes'),request.form.get('prescription'),request.form.get('followup_plan'),now()))
        eid=cur.lastrowid; conn.commit(); conn.close()
        aid,a=record_assessment({'id':eid,'symptoms':request.form.get('symptoms'),'duration':request.form.get('duration'),'temperature':request.form.get('temperature'),'bp':request.form.get('bp'),'spo2':request.form.get('spo2'),'pulse':request.form.get('pulse'),'pain':request.form.get('pain'),'notes':request.form.get('notes')},p)
        # Create follow-up if date supplied.
        if request.form.get('followup_date'):
            conn=db(); conn.execute('INSERT INTO followups(patient_id,doctor_id,due_date,reason,status,notes,created_at) VALUES(?,?,?,?,?,?,?)',(patient_id,d['id'],request.form['followup_date'],request.form.get('followup_plan') or 'Clinical follow-up','Due',request.form.get('doctor_notes',''),now())); conn.commit(); conn.close()
        flash('Encounter saved and AI clinical assessment generated.','success'); return redirect(url_for('assessment_detail',assessment_id=aid))
    return render_template('encounter.html',patient=p,doctor=d)

@app.route('/assessments')
@role_required('doctor','cmo')
def assessments():
    conn=db();
    if session.get('role')=='doctor': rows=conn.execute('SELECT a.*,p.name patient_name,p.code,e.symptoms,u.name doctor_name FROM assessments a JOIN patients p ON p.id=a.patient_id JOIN encounters e ON e.id=a.encounter_id LEFT JOIN users u ON u.id=e.provider_id WHERE p.registered_phc_id=? ORDER BY a.id DESC',(session.get('phc_id'),)).fetchall()
    else: rows=conn.execute('SELECT a.*,p.name patient_name,p.code,e.symptoms,u.name doctor_name FROM assessments a JOIN patients p ON p.id=a.patient_id JOIN encounters e ON e.id=a.encounter_id LEFT JOIN users u ON u.id=e.provider_id ORDER BY a.id DESC').fetchall()
    conn.close(); return render_template('assessments.html',assessments=rows)

@app.route('/assessments/<int:assessment_id>')
@role_required('doctor','cmo')
def assessment_detail(assessment_id):
    conn=db(); a=conn.execute('SELECT a.*,p.name patient_name,p.code,e.*,u.name doctor_name,ph.name phc_name FROM assessments a JOIN patients p ON p.id=a.patient_id JOIN encounters e ON e.id=a.encounter_id LEFT JOIN users u ON u.id=e.provider_id LEFT JOIN phcs ph ON ph.id=e.phc_id WHERE a.id=?',(assessment_id,)).fetchone(); conn.close()
    if not a: return 'Not found',404
    if session.get('role')=='doctor' and a['phc_id']!=session.get('phc_id'): return 'Forbidden',403
    return render_template('assessment_detail.html',assessment=a)

@app.route('/assessments/<int:assessment_id>/review',methods=['POST'])
@role_required('doctor')
def review_assessment(assessment_id):
    conn=db(); a=conn.execute('SELECT a.*,p.registered_phc_id FROM assessments a JOIN patients p ON p.id=a.patient_id WHERE a.id=?',(assessment_id,)).fetchone()
    if not a or a['registered_phc_id']!=session.get('phc_id'): conn.close(); return 'Forbidden',403
    conn.execute('UPDATE assessments SET doctor_review=? WHERE id=?',(request.form.get('status','Approved'),assessment_id)); conn.commit(); conn.close(); flash('Clinical review status updated.','success'); return redirect(url_for('assessment_detail',assessment_id=assessment_id))

@app.route('/referrals')
@role_required('doctor','cmo')
def referrals():
    conn=db()
    if session.get('role')=='doctor': rows=conn.execute('SELECT r.*,p.name patient_name,p.code,u.name doctor_name,ph.name phc_name FROM referrals r JOIN patients p ON p.id=r.patient_id LEFT JOIN users u ON u.id=r.from_doctor_id LEFT JOIN phcs ph ON ph.id=r.from_phc_id WHERE r.from_phc_id=? ORDER BY r.id DESC',(session.get('phc_id'),)).fetchall()
    else: rows=conn.execute('SELECT r.*,p.name patient_name,p.code,u.name doctor_name,ph.name phc_name FROM referrals r JOIN patients p ON p.id=r.patient_id LEFT JOIN users u ON u.id=r.from_doctor_id LEFT JOIN phcs ph ON ph.id=r.from_phc_id ORDER BY r.id DESC').fetchall()
    conn.close(); return render_template('referrals.html',referrals=rows)

@app.route('/referrals/new/<int:patient_id>',methods=['POST'])
@role_required('doctor')
def new_referral(patient_id):
    d=doctor_for_user(session['user_id']); conn=db(); p=conn.execute('SELECT * FROM patients WHERE id=?',(patient_id,)).fetchone()
    if not p or p['registered_phc_id']!=d['phc_id']: conn.close(); return 'Forbidden',403
    conn.execute('INSERT INTO referrals(patient_id,assessment_id,from_doctor_id,from_phc_id,facility,reason,priority,status,referred_hospital,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(patient_id,request.form.get('assessment_id') or None,d['id'],d['phc_id'],request.form.get('facility','District Hospital Lucknow'),request.form.get('reason','Specialist consultation required'),request.form.get('priority','Routine'),'Pending',request.form.get('referred_hospital',request.form.get('facility','District Hospital Lucknow')),now()))
    conn.commit(); conn.close(); flash('Referral created. You can now allocate a video consultation slot from the referral.','success'); return redirect(url_for('referrals'))

@app.route('/referrals/<int:ref_id>/consultation',methods=['GET','POST'])
@role_required('doctor')
def allocate_consultation(ref_id):
    conn=db(); r=conn.execute('SELECT r.*,p.name patient_name,p.code, p.registered_phc_id FROM referrals r JOIN patients p ON p.id=r.patient_id WHERE r.id=?',(ref_id,)).fetchone(); doctors=conn.execute("SELECT u.*,ph.name phc_name FROM users u LEFT JOIN phcs ph ON ph.id=u.phc_id WHERE u.role='doctor' AND u.id!=? ORDER BY u.name",(session['user_id'],)).fetchall(); conn.close()
    if not r or r['registered_phc_id']!=session.get('phc_id'): return 'Forbidden',403
    if request.method=='POST':
        doctor_id=request.form.get('doctor_id',type=int); scheduled=request.form.get('scheduled_at'); doctor_fee=float(request.form.get('doctor_fee') or 500); patient_fee=doctor_fee+100; platform_fee=patient_fee-doctor_fee
        if not doctor_id or not scheduled: flash('Choose a district/consulting doctor and appointment slot.','error'); return render_template('consultation_new.html',referral=r,doctors=doctors)
        room='gramcare-'+secrets.token_urlsafe(10).replace('_','-').replace('/','-')
        conn=db(); conn.execute('INSERT INTO consultations(patient_id,doctor_id,referral_id,scheduled_at,duration,room_code,doctor_fee,patient_fee,platform_fee,status,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(r['patient_id'],doctor_id,ref_id,scheduled,int(request.form.get('duration') or 30),room,doctor_fee,patient_fee,platform_fee,'Scheduled',request.form.get('notes',''),now())); conn.execute("UPDATE referrals SET status='Video Consultation Scheduled' WHERE id=?",(ref_id,)); conn.commit(); conn.close(); flash(f'Video consultation allocated. Patient fee ₹{patient_fee:.0f}; doctor fee ₹{doctor_fee:.0f}; GramCare platform fee ₹{platform_fee:.0f}.','success'); return redirect(url_for('referrals'))
    return render_template('consultation_new.html',referral=r,doctors=doctors)

@app.route('/consultations')
@login_required
def consultations():
    conn=db(); role=session.get('role')
    if role=='patient':
        p=patient_for_user(session['user_id']); rows=conn.execute('SELECT c.*,u.name doctor_name,ph.name doctor_phc FROM consultations c JOIN users u ON u.id=c.doctor_id LEFT JOIN phcs ph ON ph.id=u.phc_id WHERE c.patient_id=? ORDER BY c.scheduled_at DESC',(p['id'],)).fetchall() if p else []
    elif role=='doctor': rows=conn.execute('SELECT c.*,p.name patient_name,p.code,ph.name patient_phc FROM consultations c JOIN patients p ON p.id=c.patient_id LEFT JOIN phcs ph ON ph.id=p.registered_phc_id WHERE c.doctor_id=? OR p.registered_phc_id=? ORDER BY c.scheduled_at DESC',(session['user_id'],session.get('phc_id'))).fetchall()
    else: rows=conn.execute('SELECT c.*,p.name patient_name,p.code,u.name doctor_name,ph.name doctor_phc FROM consultations c JOIN patients p ON p.id=c.patient_id JOIN users u ON u.id=c.doctor_id LEFT JOIN phcs ph ON ph.id=u.phc_id ORDER BY c.scheduled_at DESC').fetchall()
    conn.close(); return render_template('consultations.html',consultations=rows)

@app.route('/consultations/<int:cid>')
@login_required
def consultation_room(cid):
    conn=db(); c=conn.execute('SELECT c.*,p.name patient_name,p.code,u.name doctor_name,ph.name doctor_phc FROM consultations c JOIN patients p ON p.id=c.patient_id JOIN users u ON u.id=c.doctor_id LEFT JOIN phcs ph ON ph.id=u.phc_id WHERE c.id=?',(cid,)).fetchone(); conn.close()
    if not c: return 'Not found',404
    if session.get('role')=='patient':
        p=patient_for_user(session['user_id']);
        if not p or p['id']!=c['patient_id']: return 'Forbidden',403
    if session.get('role')=='doctor' and c['doctor_id']!=session['user_id'] and c['patient_id'] not in [r['id'] for r in []]: return 'Forbidden',403
    return render_template('consultation_room.html',consultation=c)

@app.route('/consultations/<int:cid>/status',methods=['POST'])
@role_required('doctor')
def consultation_status(cid):
    conn=db(); conn.execute('UPDATE consultations SET status=?,notes=COALESCE(?,notes) WHERE id=? AND doctor_id=?',(request.form.get('status','Completed'),request.form.get('notes'),cid,session['user_id'])); conn.commit(); conn.close(); flash('Consultation status saved.','success'); return redirect(url_for('consultation_room',cid=cid))

@app.route('/followups')
@role_required('doctor','cmo')
def followups():
    conn=db();
    if session.get('role')=='doctor': rows=conn.execute('SELECT f.*,p.name patient_name,u.name doctor_name FROM followups f JOIN patients p ON p.id=f.patient_id LEFT JOIN users u ON u.id=f.doctor_id WHERE p.registered_phc_id=? ORDER BY f.due_date',(session.get('phc_id'),)).fetchall()
    else: rows=conn.execute('SELECT f.*,p.name patient_name,u.name doctor_name FROM followups f JOIN patients p ON p.id=f.patient_id LEFT JOIN users u ON u.id=f.doctor_id ORDER BY f.due_date').fetchall()
    conn.close(); return render_template('followups.html',followups=rows)

@app.route('/followups/<int:fid>/complete',methods=['POST'])
@role_required('doctor')
def complete_followup(fid):
    conn=db(); conn.execute("UPDATE followups SET status='Completed',notes=? WHERE id=? AND doctor_id=?",(request.form.get('notes',''),fid,session['user_id'])); conn.commit(); conn.close(); return redirect(url_for('followups'))

@app.route('/medicines/<int:patient_id>',methods=['GET','POST'])
@login_required
def medicines(patient_id):
    if session.get('role')=='patient':
        p=patient_for_user(session['user_id']);
        if not p or p['id']!=patient_id: return 'Forbidden',403
    if session.get('role') not in ('patient','doctor'): return 'Forbidden',403
    conn=db()
    if request.method=='POST' and session.get('role')=='doctor':
        conn.execute('INSERT INTO medicines(patient_id,prescribed_by,name,dose,schedule,duration,instructions,created_at) VALUES(?,?,?,?,?,?,?,?)',(patient_id,session['user_id'],request.form['name'],request.form.get('dose'),request.form.get('schedule'),request.form.get('duration'),request.form.get('instructions'),now())); conn.commit(); flash('Medicine added to the patient record.','success')
    rows=conn.execute('SELECT m.*,u.name doctor_name FROM medicines m LEFT JOIN users u ON u.id=m.prescribed_by WHERE m.patient_id=? ORDER BY m.id DESC',(patient_id,)).fetchall(); conn.close(); return render_template('medicines.html',patient_id=patient_id,medicines=rows)

@app.route('/reports/<int:patient_id>',methods=['GET','POST'])
@login_required
def reports(patient_id):
    if session.get('role')=='patient':
        p=patient_for_user(session['user_id']);
        if not p or p['id']!=patient_id: return 'Forbidden',403
    conn=db()
    if request.method=='POST':
        f=request.files.get('file')
        if f and f.filename:
            stored=uuid.uuid4().hex+'_'+secure_filename(f.filename); f.save(os.path.join(UPLOAD_DIR,stored)); conn.execute('INSERT INTO reports(patient_id,uploaded_by,filename,original_name,category,notes,created_at) VALUES(?,?,?,?,?,?,?)',(patient_id,session['user_id'],stored,f.filename,request.form.get('category','Medical Report'),request.form.get('notes',''),now())); conn.commit(); flash('Report added to the patient timeline.','success')
    rows=conn.execute('SELECT r.*,u.name uploaded_name FROM reports r LEFT JOIN users u ON u.id=r.uploaded_by WHERE r.patient_id=? ORDER BY r.id DESC',(patient_id,)).fetchall(); conn.close(); return render_template('reports.html',patient_id=patient_id,reports=rows)

@app.route('/reports/file/<int:report_id>')
@login_required
def report_file(report_id):
    conn=db(); r=conn.execute('SELECT * FROM reports WHERE id=?',(report_id,)).fetchone(); conn.close()
    if not r: return 'Not found',404
    if session.get('role')=='patient':
        p=patient_for_user(session['user_id']);
        if not p or p['id']!=r['patient_id']: return 'Forbidden',403
    return send_file(os.path.join(UPLOAD_DIR,r['filename']),download_name=r['original_name'])

@app.route('/community')
@role_required('doctor','cmo')
def community():
    conn=db(); base='SELECT COUNT(*) c FROM '
    data={}
    if session.get('role')=='doctor':
        phc=session.get('phc_id'); data['patients']=conn.execute('SELECT COUNT(*) c FROM patients WHERE registered_phc_id=?',(phc,)).fetchone()['c']; data['visits']=conn.execute('SELECT COUNT(*) c FROM encounters WHERE phc_id=?',(phc,)).fetchone()['c']; data['referrals']=conn.execute('SELECT COUNT(*) c FROM referrals WHERE from_phc_id=?',(phc,)).fetchone()['c']; data['admissions']=conn.execute('SELECT COUNT(*) c FROM admissions WHERE phc_id=? AND status=\'Admitted\'',(phc,)).fetchone()['c']
    else:
        data['patients']=conn.execute('SELECT COUNT(*) c FROM patients').fetchone()['c']; data['visits']=conn.execute('SELECT COUNT(*) c FROM encounters').fetchone()['c']; data['referrals']=conn.execute('SELECT COUNT(*) c FROM referrals').fetchone()['c']; data['admissions']=conn.execute("SELECT COUNT(*) c FROM admissions WHERE status='Admitted'").fetchone()['c']
    conn.close(); return render_template('community.html',data=data)

@app.route('/care-hub')
@login_required
def care_hub():
    conn=db(); facilities=conn.execute('SELECT * FROM facilities ORDER BY name').fetchall(); conn.close(); return render_template('care_hub.html',facilities=facilities)

@app.route('/facilities')
@login_required
def facilities():
    conn=db(); rows=conn.execute('SELECT * FROM facilities ORDER BY facility_type,name').fetchall(); conn.close(); return render_template('facilities.html',facilities=rows)

@app.route('/emergency',methods=['GET','POST'])
@role_required('patient')
def emergency():
    p=patient_for_user(session['user_id'])
    if request.method=='POST':
        conn=db(); conn.execute('INSERT INTO emergency_cases(patient_id,location,reason,created_at) VALUES(?,?,?,?)',(p['id'],request.form.get('location'),request.form.get('reason','Emergency assistance requested'),now())); conn.commit(); conn.close(); flash('Emergency request recorded. Seek local emergency help immediately if needed.','success'); return redirect(url_for('patient_portal'))
    return render_template('emergency.html',patient=p)

@app.route('/patient-portal')
@role_required('patient')
def patient_portal():
    p=patient_for_user(session['user_id']); conn=db();
    p=conn.execute('SELECT p.*,ph.name phc_name FROM patients p LEFT JOIN phcs ph ON ph.id=p.registered_phc_id WHERE p.id=?',(p['id'],)).fetchone() if p else None
    encounters=conn.execute('SELECT e.*,u.name doctor_name,ph.name phc_name,a.id assessment_id,a.risk_level FROM encounters e LEFT JOIN users u ON u.id=e.provider_id LEFT JOIN phcs ph ON ph.id=e.phc_id LEFT JOIN assessments a ON a.encounter_id=e.id WHERE e.patient_id=? ORDER BY e.id DESC',(p['id'],)).fetchall()
    refs=conn.execute('SELECT r.*,u.name doctor_name,ph.name phc_name FROM referrals r LEFT JOIN users u ON u.id=r.from_doctor_id LEFT JOIN phcs ph ON ph.id=r.from_phc_id WHERE r.patient_id=? ORDER BY r.id DESC',(p['id'],)).fetchall()
    consults=conn.execute('SELECT c.*,u.name doctor_name,ph.name doctor_phc FROM consultations c JOIN users u ON u.id=c.doctor_id LEFT JOIN phcs ph ON ph.id=u.phc_id WHERE c.patient_id=? ORDER BY c.scheduled_at DESC',(p['id'],)).fetchall()
    meds=conn.execute('SELECT m.*,u.name doctor_name FROM medicines m LEFT JOIN users u ON u.id=m.prescribed_by WHERE m.patient_id=? AND m.active=1 ORDER BY m.id DESC',(p['id'],)).fetchall()
    admits=conn.execute('SELECT a.*,ph.name phc_name FROM admissions a JOIN phcs ph ON ph.id=a.phc_id WHERE a.patient_id=? ORDER BY a.id DESC',(p['id'],)).fetchall()
    follow=conn.execute('SELECT f.*,u.name doctor_name FROM followups f LEFT JOIN users u ON u.id=f.doctor_id WHERE f.patient_id=? ORDER BY f.due_date',(p['id'],)).fetchall()
    notices=conn.execute('SELECT * FROM notices WHERE phc_id IS NULL OR phc_id=? ORDER BY id DESC LIMIT 5',(p['registered_phc_id'],)).fetchall()
    conn.close(); return render_template('patient_portal.html',patient=p,encounters=encounters,referrals=refs,consultations=consults,medicines=meds,admissions=admits,followups=follow,notices=notices)

# Admissions
@app.route('/admissions/new/<int:patient_id>',methods=['GET','POST'])
@role_required('doctor')
def new_admission(patient_id):
    d=doctor_for_user(session['user_id']); conn=db(); p=conn.execute('SELECT * FROM patients WHERE id=?',(patient_id,)).fetchone()
    if not p or p['registered_phc_id']!=d['phc_id']: conn.close(); return 'Forbidden',403
    if request.method=='POST':
        conn.execute('INSERT INTO admissions(patient_id,phc_id,admitted_by,bed_number,reason,condition_on_admission,admission_date,expected_discharge,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(patient_id,d['phc_id'],d['id'],request.form.get('bed_number'),request.form['reason'],request.form.get('condition'),request.form.get('admission_date') or datetime.now().date().isoformat(),request.form.get('expected_discharge'),'Admitted',now())); conn.commit(); conn.close(); flash('Patient admitted to your PHC. Daily inpatient chart is now active.','success'); return redirect(url_for('patient_detail',patient_id=patient_id))
    conn.close(); return render_template('admission_new.html',patient=p,doctor=d)

@app.route('/admissions/<int:admission_id>')
@role_required('doctor','cmo')
def admission_detail(admission_id):
    conn=db(); a=conn.execute('SELECT a.*,p.name patient_name,p.code,ph.name phc_name,u.name admitted_by_name FROM admissions a JOIN patients p ON p.id=a.patient_id JOIN phcs ph ON ph.id=a.phc_id JOIN users u ON u.id=a.admitted_by WHERE a.id=?',(admission_id,)).fetchone(); updates=conn.execute('SELECT i.*,u.name doctor_name FROM inpatient_updates i JOIN users u ON u.id=i.doctor_id WHERE i.admission_id=? ORDER BY i.update_date DESC,i.id DESC',(admission_id,)).fetchall(); conn.close()
    if not a: return 'Not found',404
    if session.get('role')=='doctor' and a['phc_id']!=session.get('phc_id'): return 'Forbidden',403
    return render_template('admission_detail.html',admission=a,updates=updates)

@app.route('/admissions/<int:admission_id>/update',methods=['POST'])
@role_required('doctor')
def inpatient_update(admission_id):
    conn=db(); a=conn.execute('SELECT * FROM admissions WHERE id=?',(admission_id,)).fetchone()
    if not a or a['phc_id']!=session.get('phc_id'): conn.close(); return 'Forbidden',403
    conn.execute('INSERT INTO inpatient_updates(admission_id,doctor_id,update_date,condition,vitals,medications,clinical_notes,created_at) VALUES(?,?,?,?,?,?,?,?)',(admission_id,session['user_id'],request.form.get('update_date') or datetime.now().date().isoformat(),request.form.get('condition'),request.form.get('vitals'),request.form.get('medications'),request.form.get('clinical_notes'),now())); conn.commit(); conn.close(); flash('Daily inpatient chart updated.','success'); return redirect(url_for('admission_detail',admission_id=admission_id))

@app.route('/admissions/<int:admission_id>/discharge',methods=['POST'])
@role_required('doctor')
def discharge(admission_id):
    conn=db(); a=conn.execute('SELECT * FROM admissions WHERE id=?',(admission_id,)).fetchone()
    if not a or a['phc_id']!=session.get('phc_id'): conn.close(); return 'Forbidden',403
    conn.execute("UPDATE admissions SET status='Discharged',discharge_date=?,discharge_summary=? WHERE id=?",(request.form.get('discharge_date') or datetime.now().date().isoformat(),request.form.get('summary',''),admission_id)); conn.commit(); conn.close(); flash('Patient discharged and discharge summary saved.','success'); return redirect(url_for('admission_detail',admission_id=admission_id))

# CMO
@app.route('/cmo')
@role_required('cmo')
def cmo_dashboard():
    conn=db();
    phcs=conn.execute('''SELECT ph.*,
        (SELECT COUNT(*) FROM patients p WHERE p.registered_phc_id=ph.id) patient_count,
        (SELECT COUNT(*) FROM encounters e WHERE e.phc_id=ph.id) visit_count,
        (SELECT COUNT(*) FROM referrals r WHERE r.from_phc_id=ph.id) referral_count,
        (SELECT COUNT(*) FROM admissions a WHERE a.phc_id=ph.id) admission_count
        FROM phcs ph ORDER BY ph.name''').fetchall()
    doctors=conn.execute('SELECT u.*,ph.name phc_name,(SELECT COUNT(*) FROM encounters e WHERE e.provider_id=u.id) visits,(SELECT COUNT(*) FROM referrals r WHERE r.from_doctor_id=u.id) referrals FROM users u LEFT JOIN phcs ph ON ph.id=u.phc_id WHERE u.role=\'doctor\' ORDER BY ph.name,u.name').fetchall()
    totals={
        'patients':conn.execute('SELECT COUNT(*) c FROM patients').fetchone()['c'],
        'visits':conn.execute('SELECT COUNT(*) c FROM encounters').fetchone()['c'],
        'referrals':conn.execute('SELECT COUNT(*) c FROM referrals').fetchone()['c'],
        'admitted':conn.execute("SELECT COUNT(*) c FROM admissions WHERE status='Admitted'").fetchone()['c'],
        'consultations':conn.execute('SELECT COUNT(*) c FROM consultations').fetchone()['c'],
        'revenue':conn.execute('SELECT COALESCE(SUM(platform_fee),0) c FROM consultations').fetchone()['c'],
    }
    notices=conn.execute('SELECT n.*,ph.name phc_name FROM notices n LEFT JOIN phcs ph ON ph.id=n.phc_id ORDER BY n.id DESC LIMIT 20').fetchall(); conn.close(); return render_template('cmo_dashboard.html',phcs=phcs,doctors=doctors,totals=totals,notices=notices)

@app.route('/cmo/notices',methods=['POST'])
@role_required('cmo')
def create_notice():
    conn=db(); conn.execute('INSERT INTO notices(cmo_id,phc_id,title,message,priority,created_at,expires_at) VALUES(?,?,?,?,?,?,?)',(session['user_id'],request.form.get('phc_id',type=int) or None,request.form['title'],request.form['message'],request.form.get('priority','Normal'),now(),request.form.get('expires_at') or None)); conn.commit(); conn.close(); flash('Notice published to the selected PHC(s).','success'); return redirect(url_for('cmo_dashboard'))

@app.route('/cmo/phcs')
@role_required('cmo')
def cmo_phcs():
    conn=db(); rows=conn.execute('SELECT * FROM phcs ORDER BY name').fetchall(); conn.close(); return render_template('cmo_phcs.html',phcs=rows)

@app.route('/cmo/doctors')
@role_required('cmo')
def cmo_doctors():
    conn=db(); rows=conn.execute('SELECT u.*,ph.name phc_name FROM users u LEFT JOIN phcs ph ON ph.id=u.phc_id WHERE u.role=\'doctor\' ORDER BY ph.name,u.name').fetchall(); conn.close(); return render_template('cmo_doctors.html',doctors=rows)

@app.route('/api/health')
def health(): return jsonify(status='ok',ai_configured=bool(GEMINI_API_KEY),model=GEMINI_MODEL)

@app.route('/api/triage-preview',methods=['POST'])
@login_required
def triage_preview(): return jsonify(triage(request.get_json(force=True) or {}))

@app.route('/offline-sync',methods=['POST'])
@role_required('doctor')
def offline_sync():
    payload=request.get_json(force=True) or {}; synced=[]; d=doctor_for_user(session['user_id'])
    for item in payload.get('items',[]):
        if item.get('type')=='encounter':
            conn=db(); conn.execute('INSERT INTO encounters(patient_id,provider_id,phc_id,symptoms,duration,temperature,bp,spo2,pulse,pain,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(item['patient_id'],d['id'],d['phc_id'],item.get('symptoms'),item.get('duration'),item.get('temperature'),item.get('bp'),item.get('spo2'),item.get('pulse'),item.get('pain'),item.get('notes'),now())); conn.commit(); conn.close(); synced.append(item.get('client_id'))
    return jsonify(ok=True,synced=synced)

if __name__=='__main__':
    init_db(); print('GramCare AI v5 | AI configured:',bool(GEMINI_API_KEY),'| model:',GEMINI_MODEL); app.run(debug=True)
else:
    init_db()
