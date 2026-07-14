# Home Vision AI

Home Vision AI is a proprietary, ultra-low-latency architectural layout compiler that transforms natural language prompts into fully buildable 3D residential floor plans in under **500 milliseconds**.

Unlike conventional AI design tools, Home Vision AI runs entirely on local infrastructure with **zero cloud dependency**, ensuring complete data privacy, offline operation, and zero marginal generation costs.

---

## Demo & Resources

* **Live MVP:** https://sanky.space
* **Technical Architecture:** [View Document](https://docs.google.com/document/d/1V8W0lc8ixE0irZ-nN6QXJztUUKMdrUGlrtw3xKb3t6E/edit?tab=t.0)
* **Video Demonstration:** [Watch on YouTube](https://youtu.be/WpDml_0v0pk)

---

## Features

### AI-Powered Layout Generation

Generate residential floor plans directly from natural language prompts.

**Example**

```text
3BHK duplex with a South-East kitchen,
attached bathrooms, and a small pooja room.
```

### Template-Based Generation

Choose from predefined regional layout templates for instant floor plan creation.

### Dynamic Geometry Engine

Modify layouts in real time using:

* Add Room
* Swap Room
* Resize Spaces

The proprietary Zero-Sum Geometry Engine automatically redistributes interior spaces while preserving the external plot boundary.

### Automated MEP Routing

Automatically generates:

* Electrical wiring layouts
* Water supply routing

using Rectilinear Minimum Spanning Trees (RMST) for efficient infrastructure planning.

### Real-Time Cost Estimation

Calculate construction costs instantly based on:

* Material quality
* Labor rates
* Layout complexity

### PDF Blueprint Export

Export finalized layouts as production-ready blueprint PDFs.

---

## System Requirements

| Requirement      | Version                  |
| ---------------- | ------------------------ |
| Python           | 3.10+                    |
| Node.js          | 18+                      |
| Ollama           | Latest                   |
| Operating System | Windows, Linux, or macOS |

---

# Installation

## 1. Local AI Model Setup

Home Vision AI uses a highly compressed Small Language Model (SLM) for architectural intent extraction and prompt understanding.

### Install Ollama

Download and install Ollama:

```text
https://ollama.com
```

Start the required model:

```bash
ollama run qwen2.5:0.5b
```

### Download the GGUF Fallback Model

Download:

```text
https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF
```

from Hugging Face and place it in the project root directory:

```text
HomeVisionAI/
├── qwen2.5-0.5b-instruct-q4_k_m.gguf
├── server.py
├── package.json
└── ...
```

---

## 2. Backend Setup

Open a terminal in the project root directory.

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Start the FastAPI Server

```bash
uvicorn server:app --reload --port 8000
```

The backend will be available at:

```text
http://localhost:8000
```

---

## 3. Frontend Setup

Open a second terminal window in the same project directory.

### Install Dependencies

```bash
npm install
```

### Start the Development Server

```bash
npm run dev
```

The frontend will typically be available at:

```text
http://localhost:5173
```

### Production Build

```bash
npm run build
```

---

## Application Workflow

```text
User Prompt
      │
      ▼
Local SLM Intent Extraction
      │
      ▼
Constraint Parser
      │
      ▼
Layout Compiler
      │
      ▼
Zero-Sum Geometry Engine
      │
      ▼
MEP Routing Engine
      │
      ▼
3D Visualization + Cost Estimation
      │
      ▼
PDF Blueprint Export
```

---

## Usage

Once both backend and frontend services are running:

### Step 1: Configure Plot Constraints

Specify:

* Plot Width
* Plot Length
* Building Type

  * Single Story
  * Duplex

### Step 2: Generate a Layout

#### AI Builder

Enter a descriptive prompt:

```text
3BHK duplex with a South-East kitchen
```

The layout will be generated instantly.

#### Template Builder

Select a predefined layout template to generate a floor plan immediately.

### Step 3: Modify Layout Geometry

Use the built-in editing tools:

* Add Room
* Swap Room
* Resize Internal Spaces

The system automatically preserves the outer property boundary while recalculating room dimensions.

### Step 4: View Infrastructure Layers

Enable MEP visualization to inspect:

* Electrical routing
* Water supply routing

### Step 5: Estimate Construction Cost

Adjust material quality and specifications to view:

* Material costs
* Labor costs
* Total estimated construction cost

in real time.

### Step 6: Export Blueprint

Generate a production-ready PDF blueprint for construction planning and documentation.

---

## Architecture Highlights

### Local-First AI

* No cloud inference
* No external API dependency
* Complete offline operation

### Ultra-Low Latency

* Average generation time below 500 milliseconds

### Constraint-Aware Layout Compilation

* Plot-aware generation
* Room adjacency enforcement
* Spatial consistency guarantees

### Automatic Infrastructure Planning

* Electrical routing
* Plumbing routing
* Buildability-focused outputs

---

## Tech Stack

### Frontend

* React
* Vite
* Three.js

### Backend

* FastAPI
* Python

### AI Layer

* Ollama
* Qwen 2.5 0.5B
* GGUF Runtime Fallback

### Computational Geometry

* Constraint Solvers
* Zero-Sum Geometry Engine
* RMST Routing Algorithms

---

## Disclaimer

Home Vision AI is intended as a design-assistance and planning tool. Generated layouts should be reviewed and approved by qualified architects, structural engineers, and local authorities before construction.

---

## License

Proprietary Software.

All rights reserved. Unauthorized copying, modification, distribution, or commercial use is prohibited without explicit permission from the authors.
