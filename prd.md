PRD: Agentic Software Factory
System Codename: FactoryBot

1. Vision & Philosophy
FactoryBot is a self-hosted, agent-based software development factory that replicates the client-agency relationship—without the overhead. It runs entirely on a Raspberry Pi 5 (8GB RAM) and is operated through a Telegram bot interface.
The philosophy is simple: the human provides the creative vision and final approval; the agents do the heavy lifting. The user pitches ideas conversationally, iterates through brainstorming, approves a detailed PRD, and then agents take ownership of development and deployment. The user stays involved in key decisions (technical architecture, design direction, feature prioritization) but never gets bogged down in implementation details.
Every project starts messy and gets refined. Version one ships with sensible defaults and placeholder content. Perfection is not the goal—shipping is.

2. System Architecture Overview
2.1 Host Environment

Hardware: Raspberry Pi 5, 8GB RAM
OS: Linux (Coolify-based deployment infrastructure already in place)
Capability: Agents have full sudo/root access to install any software, libraries, runtimes, databases, or tools required for any given project.

2.2 Core Components
ComponentRoleTelegram Bot InterfacePrimary communication channel between user and all agentsAgent 1: Ideation AgentBrainstorms, challenges, and refines raw ideas with the userAgent 2: PRD AgentGenerates detailed, development-ready PRDs from finalized ideasAgent 3: Development AgentBuilds the project, makes technical decisions, asks user for input on key choicesAgent 4: Deployment AgentHandles self-deployment, secrets management, DNS, SSL, and production readinessProject Memory StorePersistent documentation of decisions, rationale, and project state for future reference
2.3 Communication Flow
User (Telegram)
    │
    ▼
┌──────────────────┐
│  Telegram Bot     │ ◄── Single entry point for all interactions
│  (Router/Orchestr)│
└──────┬───────────┘
       │
       ├──► Ideation Agent ──► PRD Agent ──► Development Agent ──► Deployment Agent
       │         ▲                  ▲                ▲                    ▲
       │         │                  │                │                    │
       └─────────┴──────────────────┴────────────────┴────────────────────┘
                        User feedback loop via Telegram

3. Detailed Workflow
Phase 1: Idea Intake & Brainstorming
Trigger: User sends a message to the Telegram bot describing a new idea.
Agent: Ideation Agent
Behavior:
The Ideation Agent acts as a sharp, honest creative partner. When the user pitches an idea (e.g., "Let's build a wedding RSVP site where guests can confirm attendance and the couple can manage the guest list and send reminders"), the agent:

Acknowledges the idea and restates it to confirm understanding.
Asks for the project email address that will be associated with this project. This is collected before any brainstorming begins.
Asks targeted clarifying questions—not a dump of 20 questions, but 2-3 at a time, conversationally.
Challenges weak spots: "What happens if a guest RSVPs twice?" or "Do you need multi-language support?"
Proposes features the user may not have considered: "Should guests be able to add a plus-one with dietary restrictions?"
Flags scope creep early: "That sounds like a Phase 2 feature. Let's keep V1 focused."

Exit Criteria: User explicitly says the idea is finalized (e.g., "Looks good, let's move to the PRD").
Output: A structured idea summary saved to the Project Memory Store.

Phase 2: PRD Generation
Trigger: User approves the finalized idea.
Agent: PRD Agent
Behavior:
The PRD Agent takes the structured idea summary and generates a comprehensive, development-ready PRD. This PRD is detailed enough that a developer (human or AI) can build from it without additional context. It includes:

Project overview and goals
User personas and use cases
Feature list with priority levels (Must-have, Nice-to-have, Future)
Technical architecture recommendations — the agent proposes specific choices and asks the user via Telegram:

"Frontend: React with Tailwind or vanilla HTML/CSS?"
"Backend: Node.js with Express or Python with FastAPI?"
"Database: SQLite for simplicity or PostgreSQL for scale?"
"Do you want this as a PWA with offline support?"
"Color palette preference? I suggest a clean, modern look—here are 3 options."


API design (endpoints, data models)
Deployment requirements (domain, SSL, environment variables)
V1 scope definition — clearly states what ships in V1 and what's deferred
Placeholder strategy — V1 uses placeholder text (contextually appropriate), placeholder images, and default branding unless the user provides assets

User Interaction: The PRD Agent sends the draft PRD via Telegram (or as a file if it's long). The user can approve it as-is, request changes ("Make the API RESTful instead of GraphQL"), or ask questions ("Why did you choose SQLite?").
Exit Criteria: User explicitly approves the PRD.
Output: Final PRD saved as a markdown file in the project directory and to the Project Memory Store.

Phase 3: Development
Trigger: User approves the PRD.
Agent: Development Agent
Behavior:
The Development Agent owns the build. It follows the PRD faithfully and makes autonomous decisions on implementation details. Specifically:

Sets up the project structure — initializes the repo, installs dependencies, configures the development environment.
Installs any required software on the Raspberry Pi (runtimes, databases, build tools, etc.) without asking permission—it has full authority to do so.
Builds features incrementally — works through the PRD feature list in priority order.
Uses sensible defaults everywhere:

Placeholder text and images for content not yet provided
Default color palette from the PRD
Standard responsive breakpoints
Common security practices (input validation, CSRF protection, etc.)


Asks the user only for key decisions via Telegram:

"The PRD says email notifications—do you want to use SendGrid, Resend, or a self-hosted SMTP?"
"I'm implementing the guest list. Should there be a max guest limit per invitation?"
These questions are concise and include a recommended default: "I suggest Resend for simplicity. Should I proceed with that?"


Reports progress via Telegram at meaningful milestones: "Frontend scaffolding done. Working on the RSVP form now."
Handles errors autonomously — if a build fails or a dependency has issues, the agent debugs and fixes without involving the user unless it's truly stuck (in which case it pings via Telegram with a clear description of the blocker).

Scope Creep Guardian: If the user sends a message during development that constitutes scope creep (e.g., "Can we also add a photo gallery where guests upload pictures?"), the Development Agent flags it: "That sounds like a great Phase 2 feature. I'd recommend we ship V1 first and revisit this. Want me to note it for later?" Unless the user explicitly overrides this, it stays out of V1.
Output: A fully built, tested, deployable application.

Phase 4: Deployment
Trigger: Development Agent declares the build complete.
Agent: Deployment Agent
Behavior:
The Deployment Agent handles everything needed to put the project into production on the Raspberry Pi:

Configures the production environment — environment variables, secrets, API keys, tokens.
Sets up the web server — Nginx, Caddy, or whatever is appropriate.
Handles SSL/TLS — sets up certificates (Let's Encrypt or Coolify-managed).
Configures DNS if applicable (or provides instructions for external DNS).
Deploys the application via Coolify or Docker, depending on the project.
Runs smoke tests — verifies the deployment is working.
Notifies the user via Telegram: "Your project is live at [URL]. Here's a summary of what's deployed."

Output: A running production application accessible via a URL.

Phase 5: Post-Deployment Documentation
Trigger: Successful deployment.
Agent: Any agent (automated process)
Behavior:
After deployment, the system automatically generates and saves a Project Decision Log that includes:

Project name, email, and description
Key decisions made and why (e.g., "Chose SQLite because V1 doesn't need multi-user writes at scale")
Technical stack summary (languages, frameworks, databases, services)
V1 feature list (what shipped)
Deferred features (what was explicitly pushed to later versions)
Known limitations
Deployment details (URL, server config, environment)
How to revisit — clear instructions on how to pick this project back up

This document is the single source of truth for the project and is what agents reference when the user revisits the project in the future.
Output: PROJECT_LOG.md saved in the project directory and indexed in the Project Memory Store.

4. Project Revisitation
At any time, the user can message the Telegram bot and say something like: "Let's revisit the wedding RSVP project. I want to add a photo gallery."
The system:

Loads the Project Decision Log for that project.
Provides the relevant agent with full context on what was built, what decisions were made, and what was deferred.
The agent picks up the conversation naturally: "Welcome back! The wedding RSVP project is running at [URL]. You had deferred a photo gallery feature. Want to scope that out now?"
The same workflow applies: brainstorm → PRD update → development → deployment.


5. Telegram Bot Interface Specifications
5.1 Commands
CommandAction/newStart a new project idea/projectsList all active and deployed projects/revisit [project_name]Revisit an existing project/status [project_name]Get current status of a project/approveApprove the current phase (idea, PRD, deployment)/pausePause the current project/resumeResume a paused project
5.2 Natural Language Support
The bot should understand natural language as well. If the user sends "I have an idea for a recipe app," the bot should route that to the Ideation Agent without requiring /new first. Commands are shortcuts, not requirements.
5.3 Notifications

Progress updates are sent proactively during development at meaningful milestones.
Decision requests are sent when agents need user input on key choices.
Blockers are escalated immediately with a clear description of the problem.
Deployment confirmations are sent when a project goes live.


6. Project Memory Store
6.1 Structure
Each project gets a directory:
/projects/
├── wedding-rsvp/
│   ├── IDEA_SUMMARY.md        # Finalized idea from Phase 1
│   ├── PRD.md                 # Approved PRD from Phase 2
│   ├── PROJECT_LOG.md         # Post-deployment decision log
│   ├── src/                   # Project source code
│   └── .env                   # Environment variables and secrets
├── recipe-app/
│   ├── ...
└── ...
6.2 Memory Retrieval
When revisiting a project, agents read PROJECT_LOG.md first to understand the full history. They do not replay entire conversation logs—they rely on the structured decision log as the single source of truth.

7. Technical Constraints & Defaults
7.1 Hardware Constraints

RAM: 8GB — agents must be mindful of memory usage. Prefer lightweight frameworks and databases.
Storage: Limited — agents should clean up build artifacts and use .dockerignore or equivalent to minimize disk usage.
CPU: ARM-based — all dependencies must be ARM-compatible.

7.2 Default Technical Choices (unless user overrides)
DecisionDefaultFrontendHTML/CSS/JS with Tailwind (lightweight, no build step)BackendNode.js with Express or Python with FastAPIDatabaseSQLite for simple projects, PostgreSQL for complex onesDeploymentDocker via CoolifyWeb serverCaddy (automatic HTTPS)Version controlGit (local repo on Pi)Email serviceResend (free tier)Placeholder imagespicsum.photos or local SVG placeholdersPlaceholder textContextually appropriate placeholder (not Lorem Ipsum when possible)
7.3 Security Defaults

All secrets stored in .env files, never hardcoded.
HTTPS enforced on all deployed projects.
Input validation on all user-facing forms.
Rate limiting on APIs.
CORS configured per project needs.


8. Agent Behavior Principles

Ship V1 fast. Perfection is the enemy of done. Use placeholders, defaults, and minimal viable features.
Ask, don't assume—but only for the big stuff. Technical implementation details are the agent's domain. Design direction and feature decisions involve the user.
Guard the scope. Flag scope creep immediately. Advocate for deferring non-essential features. Only back down if the user explicitly overrides.
Document everything. Every decision, every trade-off, every deferral gets logged.
Be conversational. Telegram messages should feel like chatting with a smart colleague, not reading a corporate memo.
Fail gracefully. If something breaks, fix it. If you can't fix it, explain the blocker clearly and suggest options.
Respect the hardware. This is a Raspberry Pi, not a cloud server. Be mindful of resource usage at all times.


9. Success Criteria
The system is successful when:

A user can go from idea to deployed application entirely through Telegram messages.
Projects are revisitable months later with full context preserved.
The user feels involved in decisions without being overwhelmed by implementation details.
V1 of any project ships within a reasonable timeframe with sensible defaults.
The Raspberry Pi remains stable and responsive even with multiple deployed projects.


10. Open Questions for Implementation
These are decisions to be made by the implementing agent:

LLM Backend: Which model powers the agents? Open AI API

Telegram Bot Framework: python-telegram-bot, Telegram (Node.js), or another framework?
Agent Orchestration: How do agents hand off between phases? Simple state machine, task queue, or orchestration framework?  There must be an archestrationg agent that follows direction from a main document

Concurrent Projects: Can the user work on multiple projects simultaneously, or one at a time? one at at cime
Budget/Cost Constraints: If using cloud LLM APIs, what's the acceptable monthly cost? up until 100 dollars per month.


This PRD is the single source of truth for building FactoryBot. The implementing agent should read this file in its entirety before writing any code. When in doubt, refer back to this document. When the document is ambiguous, make a sensible default decision and document it.

install all needed skills , check claude.md 

