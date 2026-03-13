# Vectrax CLI Installation Guide

## ✅ Installation Complete!

The `vx` CLI tool has been successfully installed in your Vectrax environment.

## 🚀 Quick Start

### Option 1: Using the activation script (Recommended)
```bash
cd ~/Vectrax
source activate_vx.sh
vx help
```

### Option 2: Manual activation
```bash
cd ~/Vectrax
source .venv/bin/activate
vx help
```

## 📖 Usage

### Check Status
```bash
vx status
```

### List Available Models
```bash
vx models
```

### Start Services
```bash
vx start
```

### Generate AI Response
```bash
vx "Explain quantum computing in simple terms"
```

### Use Specific Model
```bash
vx "Write a Python function to calculate fibonacci" --model qwen2.5-coder:7b
```

## 🔧 Advanced Setup

### Make vx available globally (Optional)

To use `vx` from anywhere without activating the virtual environment:

#### Option A: Add to PATH (macOS/Linux)
Add this line to your `~/.zshrc` or `~/.bashrc`:
```bash
export PATH="/Users/mariobravo/Vectrax/.venv/bin:$PATH"
```

Then reload your shell:
```bash
source ~/.zshrc  # or source ~/.bashrc
```

#### Option B: Create a symlink (macOS/Linux)
```bash
sudo ln -s /Users/mariobravo/Vectrax/.venv/bin/vx /usr/local/bin/vx
```

#### Option C: Create an alias
Add this to your `~/.zshrc` or `~/.bashrc`:
```bash
alias vx='/Users/mariobravo/Vectrax/.venv/bin/vx'
```

## 🧪 Verify Installation

Run these commands to verify everything is working:

```bash
# Check vx is available
which vx

# Test help command
vx help

# Check system status
vx status

# List models
vx models
```

## 📦 Dependencies

The following dependencies are installed:
- httpx - HTTP client
- pyyaml - YAML configuration
- fastapi - API framework
- uvicorn - ASGI server
- sentence-transformers - Embeddings
- torch - ML framework

## 🛠️ Troubleshooting

### "vx: command not found"
Make sure you've activated the virtual environment:
```bash
source activate_vx.sh
```

### "Ollama is not running"
Start Ollama with:
```bash
brew services start ollama
# or
vx start
```

### Missing dependencies
Reinstall the package:
```bash
cd ~/Vectrax
source .venv/bin/activate
pip install -e .
```

## 📚 Next Steps

- Read the [README.md](README.md) for full documentation
- Explore the [docs/](docs/) directory for phase documentation
- Try the example workflows in the README

## 🎉 You're Ready!

Start using Vectrax with:
```bash
vx "Hello, world!"
```

Happy coding! 🚀
