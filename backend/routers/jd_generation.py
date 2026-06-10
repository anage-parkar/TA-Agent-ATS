"""Router for AI-based Job Description generation."""

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from agents.jd_generator import generate_jd
from db.repository import create_generated_jd, get_generated_jd, list_generated_jds
from models.jd_generation import GeneratedJD, JDContent, JDGenerationRequest
from services.llm_client import LLMError

logger = logging.getLogger("ta_agent.routers.jd_generation")

router = APIRouter(tags=["jd-generation"])

# ── Static metadata ────────────────────────────────────────────────────────────

COMMON_SKILLS = ["Git", "Postman", "JIRA", "Confluence", "Agile / Scrum", "Code Review"]

SKILLS_BY_ROLE: dict[str, list[str]] = {
    # ── Platform Engineering ──────────────────────────────────────────
    "AI Engineer": [
        "Python", "LangChain / LangGraph", "OpenAI / Claude API",
        "Vector Databases (Pinecone, ChromaDB, Weaviate)", "RAG (Retrieval Augmented Generation)",
        "Fine-tuning LLMs", "TensorFlow / PyTorch", "FastAPI",
        "Hugging Face Transformers", "Prompt Engineering",
        "MLflow / Weights & Biases", "Embeddings & Semantic Search",
        "NLP Fundamentals", "Model Evaluation & Benchmarking",
        "Agentic AI Frameworks", "Docker / Kubernetes",
        "Cloud (AWS / GCP / Azure)", "REST APIs",
        *COMMON_SKILLS,
    ],
    ".NET Developer": [
        "C#", "ASP.NET Core", "Entity Framework Core", "LINQ",
        "SQL Server", "REST APIs", "Microservices Architecture",
        "Azure", "gRPC", "Blazor", "SignalR",
        "NUnit / xUnit", "SOLID Principles", "Design Patterns",
        "Docker", "Dependency Injection", "CI/CD Pipelines",
        *COMMON_SKILLS,
    ],
    "Python Developer": [
        "Python (Advanced)", "Django", "FastAPI", "Flask",
        "SQLAlchemy", "Celery", "Redis", "PostgreSQL / MySQL",
        "REST APIs", "Microservices Architecture",
        "Docker", "Kubernetes", "AWS / GCP",
        "Pytest / Unit Testing", "NumPy / Pandas",
        "Type Hints / Mypy", "Asynchronous Programming (asyncio)",
        "CI/CD Pipelines",
        *COMMON_SKILLS,
    ],
    "Java Developer": [
        "Java (Core + Advanced)", "Spring Boot", "Spring MVC",
        "Hibernate / JPA", "REST APIs", "Microservices Architecture",
        "SQL (MySQL / PostgreSQL)", "Maven / Gradle",
        "JUnit / Mockito", "Docker", "Kubernetes",
        "AWS / GCP / Azure", "Message Queues (Kafka / RabbitMQ)",
        "Redis", "Design Patterns", "Stream API / Lambda Expressions",
        "Multithreading / Concurrency", "CI/CD Pipelines",
        *COMMON_SKILLS,
    ],
    "MERN Stack Developer": [
        "MongoDB", "Express.js", "React.js", "Node.js",
        "JavaScript (ES6+)", "TypeScript", "REST APIs", "GraphQL",
        "Redux / Zustand", "JWT Authentication", "Next.js",
        "Tailwind CSS", "AWS / GCP", "Docker",
        "Webpack / Vite", "Jest / React Testing Library", "CI/CD Pipelines",
        *COMMON_SKILLS,
    ],
    "React Developer": [
        "React.js", "TypeScript", "JavaScript (ES6+)", "Next.js",
        "Redux / Zustand / Context API", "React Query / TanStack Query",
        "Tailwind CSS / Material UI", "REST APIs / GraphQL",
        "Jest / Vitest", "Cypress / Playwright (E2E)",
        "Webpack / Vite", "Performance Optimization",
        "Responsive Design", "Storybook",
        "Server-Side Rendering (SSR)", "CI/CD Pipelines",
        *COMMON_SKILLS,
    ],
    "Angular Developer": [
        "Angular (latest)", "TypeScript", "RxJS", "NgRx / Akita",
        "Angular Material / PrimeNG", "REST APIs",
        "Jasmine / Karma / Jest", "Node.js", "SCSS / CSS",
        "Lazy Loading", "Service Workers / PWA",
        "Web Components", "CI/CD Pipelines",
        *COMMON_SKILLS,
    ],
    "Node.js Developer": [
        "Node.js", "Express.js", "TypeScript", "REST APIs", "GraphQL",
        "MongoDB / PostgreSQL", "Redis", "JWT / OAuth",
        "Microservices Architecture", "Message Queues (Kafka / RabbitMQ)",
        "Jest / Mocha", "Docker / Kubernetes",
        "AWS / GCP", "WebSockets", "Serverless (Lambda)",
        *COMMON_SKILLS,
    ],
    "Full Stack Developer": [
        "React.js / Angular", "Node.js / Python", "TypeScript",
        "REST APIs / GraphQL", "SQL / NoSQL Databases",
        "Docker / Kubernetes", "AWS / GCP / Azure",
        "Microservices Architecture", "Authentication (JWT, OAuth)",
        "Unit / Integration / E2E Testing",
        "Performance Optimization", "Serverless Architecture", "CI/CD Pipelines",
        *COMMON_SKILLS,
    ],
    # ── Data Engineering ──────────────────────────────────────────────
    "Data Engineer": [
        "Python", "PySpark / Apache Spark", "Apache Kafka",
        "Apache Airflow", "dbt (data build tool)", "SQL (Advanced)",
        "Snowflake / BigQuery / Redshift", "AWS / GCP / Azure",
        "ETL / ELT Pipelines", "Data Modeling",
        "Delta Lake / Apache Iceberg", "Databricks",
        "Hadoop Ecosystem", "Data Quality & Validation",
        "Streaming Data Processing",
        *COMMON_SKILLS,
    ],
    "Data Analyst": [
        "SQL (Advanced)", "Python (Pandas, NumPy)", "Tableau / Power BI",
        "Excel / Google Sheets", "Statistical Analysis",
        "Data Visualization", "A/B Testing", "Google Analytics",
        "R (Basics)", "Machine Learning Basics",
        "ETL Tools", "Business Intelligence", "Data Storytelling",
        *COMMON_SKILLS,
    ],
    "ML Engineer": [
        "Python", "TensorFlow / PyTorch", "scikit-learn",
        "MLflow / Kubeflow", "Feature Engineering",
        "Model Training & Evaluation", "Model Serving (FastAPI, Flask)",
        "Docker / Kubernetes", "CI/CD for ML (MLOps)",
        "Feature Stores (Feast, Tecton)", "Data Pipelines",
        "A/B Testing for Models", "GPU Computing (CUDA)", "SQL / NoSQL",
        *COMMON_SKILLS,
    ],
    "Data Scientist": [
        "Python (Advanced)", "Machine Learning Algorithms",
        "Deep Learning (TensorFlow / PyTorch)", "Statistical Modeling",
        "NumPy / Pandas / SciPy", "Scikit-learn", "SQL",
        "Data Visualization (Matplotlib, Seaborn)",
        "Natural Language Processing (NLP)", "Experiment Design",
        "Time Series Analysis", "Business Intelligence",
        *COMMON_SKILLS,
    ],
    "Data Architect": [
        "Data Modeling (ER, Dimensional)", "Data Warehousing",
        "Data Lakes / Lakehouses", "SQL / NoSQL Databases",
        "Snowflake / BigQuery / Redshift", "Azure Data Factory / AWS Glue",
        "Databricks / Apache Spark", "Master Data Management (MDM)",
        "Data Governance", "ETL/ELT Architecture",
        "Cloud Architecture (AWS / Azure / GCP)", "Metadata Management",
        *COMMON_SKILLS,
    ],
    # ── DevOps ────────────────────────────────────────────────────────
    "DevOps Engineer": [
        "Kubernetes", "Docker", "Jenkins / GitHub Actions / GitLab CI",
        "Terraform / Pulumi", "Ansible / Chef / Puppet",
        "AWS / GCP / Azure", "Linux Administration",
        "Python / Bash Scripting", "CI/CD Pipelines",
        "Prometheus / Grafana", "ELK Stack",
        "Networking & Security", "Helm Charts", "ArgoCD / Flux (GitOps)",
        *COMMON_SKILLS,
    ],
    "Cloud Engineer": [
        "AWS / Azure / GCP (Multi-cloud)", "Terraform / CloudFormation",
        "Kubernetes / EKS / GKE / AKS", "Docker",
        "Infrastructure as Code (IaC)",
        "Networking (VPC, Load Balancers, DNS)",
        "Security (IAM, Encryption, Compliance)",
        "Python / Bash Scripting", "CI/CD Pipelines",
        "Serverless (Lambda, Cloud Functions)",
        "Cost Optimization", "Disaster Recovery", "Cloud Monitoring",
        *COMMON_SKILLS,
    ],
    "Site Reliability Engineer": [
        "Kubernetes", "Docker", "Monitoring (Prometheus, Grafana, Datadog)",
        "Incident Management", "Python / Go",
        "SLO / SLA / Error Budgets", "Chaos Engineering",
        "CI/CD Pipelines", "Linux Administration",
        "Distributed Systems", "Performance Tuning",
        "Runbook Automation", "On-call Response",
        *COMMON_SKILLS,
    ],
    "Platform Engineer": [
        "Kubernetes / Helm", "Docker", "AWS / GCP / Azure",
        "Terraform / Pulumi", "Internal Developer Platforms (IDP)",
        "Service Mesh (Istio, Linkerd)", "GitOps (ArgoCD, FluxCD)",
        "Python / Go / Bash", "Observability (OpenTelemetry, Jaeger)",
        "Security (OPA, Vault)", "CI/CD (GitHub Actions, Tekton)",
        "Developer Experience (DX)",
        *COMMON_SKILLS,
    ],
    # ── QA Engineering ────────────────────────────────────────────────
    "QA Engineer": [
        "Manual Testing", "Test Case Design", "Selenium WebDriver",
        "API Testing (Postman, RestAssured)", "SQL",
        "JIRA / TestRail", "Agile / Scrum", "Regression Testing",
        "Defect Tracking", "Test Planning", "BDD (Cucumber)",
        "Python / Java basics", "CI/CD basics",
        *COMMON_SKILLS,
    ],
    "Automation Test Engineer": [
        "Selenium WebDriver", "Cypress / Playwright",
        "TestNG / JUnit / pytest", "Java / Python / JavaScript",
        "REST API Automation (RestAssured, Supertest)",
        "CI/CD Integration (Jenkins, GitHub Actions)",
        "BDD (Cucumber, Gherkin)", "Performance Testing (JMeter)",
        "Docker basics", "SQL", "Page Object Model (POM)",
        "Cross-browser Testing",
        *COMMON_SKILLS,
    ],
    # ── Mobile Engineering ────────────────────────────────────────────
    "iOS Developer": [
        "Swift", "SwiftUI", "UIKit", "Xcode",
        "REST APIs / GraphQL", "Core Data / Realm",
        "Combine / Async-Await", "Push Notifications (APNs)",
        "App Store Publishing", "Unit Testing (XCTest)",
        "MVVM / Clean Architecture", "CocoaPods / Swift Package Manager",
        *COMMON_SKILLS,
    ],
    "Android Developer": [
        "Kotlin", "Android SDK", "Jetpack Compose", "Android Studio",
        "REST APIs / GraphQL", "Room Database",
        "Coroutines / Flow", "Push Notifications (FCM)",
        "Google Play Publishing", "JUnit / Espresso",
        "MVVM / Clean Architecture", "Gradle / Hilt (DI)",
        *COMMON_SKILLS,
    ],
    "Flutter Developer": [
        "Flutter", "Dart", "State Management (Provider, Riverpod, BLoC)",
        "REST APIs", "Firebase", "SQLite / Hive",
        "Platform Channels (iOS & Android)", "Push Notifications",
        "App Store & Play Store Publishing",
        "Unit / Widget / Integration Testing",
        "Material Design / Cupertino",
        *COMMON_SKILLS,
    ],
    "React Native Developer": [
        "React Native", "JavaScript / TypeScript", "React Navigation",
        "Redux / Zustand", "REST APIs / GraphQL",
        "AsyncStorage / SQLite", "Push Notifications (FCM / APNs)",
        "Expo / Bare Workflow", "Native Modules (iOS + Android)",
        "Jest / Detox Testing",
        *COMMON_SKILLS,
    ],
}

BUSINESS_UNITS: dict[str, list[str]] = {
    "Platform Engineering": [
        "AI Engineer", ".NET Developer", "Python Developer", "Java Developer",
        "MERN Stack Developer", "React Developer", "Angular Developer",
        "Node.js Developer", "Full Stack Developer",
    ],
    "Data Engineering": [
        "Data Engineer", "Data Analyst", "ML Engineer",
        "Data Scientist", "Data Architect",
    ],
    "DevOps": [
        "DevOps Engineer", "Cloud Engineer",
        "Site Reliability Engineer", "Platform Engineer",
    ],
    "QA Engineering": [
        "QA Engineer", "Automation Test Engineer",
    ],
    "Mobile Engineering": [
        "iOS Developer", "Android Developer",
        "Flutter Developer", "React Native Developer",
    ],
}

# Years of experience → allowed designations
DESIGNATION_MAP: dict[int, list[str]] = {
    0: ["Intern", "Graduate Trainee Engineer (GTE)"],
    1: ["SE1"],
    2: ["SE2"],
    3: ["SE2", "SE3"],
    4: ["SE3"],
    5: ["SE4"],
    6: ["SE4"],
    7: ["SE4"],
    8: ["Associate", "Lead"],
    9: ["Associate", "Lead"],
    10: ["Associate", "Lead"],
}
# 11 years and above
DESIGNATION_MAP_HIGH = ["Lead", "Principal / Architect", "Senior Lead"]


def get_designations(years: int) -> list[str]:
    if years <= 10:
        return DESIGNATION_MAP.get(years, ["SE4"])
    return DESIGNATION_MAP_HIGH


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/api/jd/metadata")
def jd_metadata():
    """Return all business units, roles, skills-by-role and designation mapping."""
    return {
        "business_units": BUSINESS_UNITS,
        "skills_by_role": SKILLS_BY_ROLE,
        "designation_map": {
            **{str(k): v for k, v in DESIGNATION_MAP.items()},
            "11+": DESIGNATION_MAP_HIGH,
        },
    }


@router.get("/api/jd/designations")
def get_designations_for_experience(years: int = 0):
    """Return allowed designations for a given years-of-experience value."""
    return {"years": years, "designations": get_designations(years)}


def _jd_to_response(row: dict) -> dict:
    """Normalise a DB row (or in-memory dict) into a consistent response shape.

    DB returns JSONB columns as already-parsed Python objects (psycopg3
    auto-decodes jsonb → dict/list). We strip pdf_base64 from list responses
    to keep payloads small; it's available on the individual GET endpoint.
    """
    content = row.get("content") or {}
    if isinstance(content, str):
        content = json.loads(content)

    skills = row.get("skills") or []
    if isinstance(skills, str):
        skills = json.loads(skills)

    return {
        "jd_id": str(row.get("id") or row.get("jd_id", "")),
        "business_unit": row.get("business_unit", ""),
        "role": row.get("role", ""),
        "designation": row.get("designation", ""),
        "years_of_experience": row.get("years_of_experience", 0),
        "skills": skills,
        "content": content,
        "pdf_url": row.get("pdf_url"),
        "created_at": str(row.get("created_at", "")),
    }


@router.get("/api/jd")
def list_jds():
    """List all generated JDs (newest first), without the pdf_base64 payload."""
    rows = list_generated_jds()
    items = [_jd_to_response(r) for r in rows]
    return {"jds": items, "count": len(items)}


@router.get("/api/jd/{jd_id}")
def get_jd(jd_id: str):
    """Retrieve a specific generated JD by ID (includes pdf_base64 if available)."""
    row = get_generated_jd(jd_id)
    if not row:
        raise HTTPException(status_code=404, detail="JD not found")
    resp = _jd_to_response(row)
    # Include base64 on individual GET so the frontend can offer offline viewing
    resp["pdf_base64"] = row.get("pdf_base64")
    return resp


@router.post("/api/jd/generate")
def generate_jd_endpoint(req: JDGenerationRequest):
    """Generate a JD with AI, render a PDF, persist everything to Supabase, and return it."""
    bu_roles = BUSINESS_UNITS.get(req.business_unit)
    if not bu_roles:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown business unit '{req.business_unit}'. "
                   f"Valid options: {list(BUSINESS_UNITS)}",
        )
    if req.role not in bu_roles:
        raise HTTPException(
            status_code=400,
            detail=f"Role '{req.role}' not found under '{req.business_unit}'.",
        )

    allowed = get_designations(req.years_of_experience)
    if req.designation not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Designation '{req.designation}' is not valid for "
                   f"{req.years_of_experience} year(s) of experience. "
                   f"Allowed: {allowed}",
        )

    # ── Generate JD content via LLM ───────────────────────────────────
    try:
        content: JDContent = generate_jd(req)
    except LLMError as exc:
        logger.error("JD generation failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"LLM generation failed: {exc}") from exc

    jd_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # ── Generate PDF and encode as base64 ─────────────────────────────
    uploads_dir = Path(__file__).resolve().parent.parent / "uploads" / "jds"
    pdf_url: str | None = None
    pdf_base64: str | None = None

    jd_obj = GeneratedJD(
        jd_id=jd_id,
        business_unit=req.business_unit,
        role=req.role,
        designation=req.designation,
        years_of_experience=req.years_of_experience,
        skills=req.skills,
        content=content,
        pdf_url=None,
        created_at=now,
    )

    try:
        from services.pdf_generator import generate_jd_pdf

        pdf_path = generate_jd_pdf(jd_obj, uploads_dir)
        pdf_url = f"/uploads/jds/{jd_id}.pdf"
        pdf_base64 = base64.b64encode(pdf_path.read_bytes()).decode("utf-8")
        logger.info("PDF generated and encoded: %s (%d bytes)", pdf_path.name, pdf_path.stat().st_size)
    except Exception as exc:
        logger.warning("PDF generation failed (JD still saved without PDF): %s", exc)

    jd_obj.pdf_url = pdf_url

    # ── Persist to Supabase (falls back to in-memory if DB unavailable) ──
    try:
        create_generated_jd({
            "id": jd_id,
            "business_unit": req.business_unit,
            "role": req.role,
            "designation": req.designation,
            "years_of_experience": req.years_of_experience,
            "skills": req.skills,
            "content": content.model_dump(),
            "pdf_base64": pdf_base64,
            "pdf_url": pdf_url,
            "created_at": now,
        })
        logger.info(
            "JD persisted: id=%s role=%s BU=%s designation=%s",
            jd_id, req.role, req.business_unit, req.designation,
        )
    except Exception as exc:
        logger.error("Failed to persist JD to DB: %s", exc)
        raise HTTPException(status_code=500, detail=f"DB persist failed: {exc}") from exc

    return jd_obj.model_dump()


@router.get("/api/jd/{jd_id}/download")
def download_jd_pdf(jd_id: str):
    """Stream the PDF for a generated JD as a file download."""
    row = get_generated_jd(jd_id)
    if not row:
        raise HTTPException(status_code=404, detail="JD not found")

    # Prefer the local file (faster); fall back to decoding the stored base64
    pdf_path = Path(__file__).resolve().parent.parent / "uploads" / "jds" / f"{jd_id}.pdf"

    if not pdf_path.exists():
        b64 = row.get("pdf_base64")
        if not b64:
            raise HTTPException(status_code=404, detail="PDF not available for this JD")
        # Reconstruct the file from the stored base64
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(base64.b64decode(b64))

    role_slug = str(row.get("role", "role")).replace(" ", "_").replace("/", "-")
    desig_slug = str(row.get("designation", "")).replace(" ", "_").replace("/", "-")
    filename = f"JD_{role_slug}_{desig_slug}.pdf"
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=filename,
    )
