#!/bin/bash
#
# start-local-llm.sh - Start local LLM server for tax processing
#
# This script starts llama.cpp server with recommended settings
# for the Ryzen AI MAX+ 395 or similar high-memory systems.
#
# Usage:
#   ./start-local-llm.sh [model-path]
#
# If no model path is provided, it will look for models in ./models/

set -e

# Configuration
LLAMA_CPP_DIR="${LLAMA_CPP_DIR:-$HOME/llama.cpp}"
MODEL_DIR="${MODEL_DIR:-./models}"
PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"
CTX_SIZE="${CTX_SIZE:-32768}"
THREADS="${THREADS:-16}"
GPU_LAYERS="${GPU_LAYERS:-99}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================${NC}"
echo -e "${BLUE}   Local LLM Server for Tax Processing${NC}"
echo -e "${BLUE}======================================${NC}"
echo ""

# Check for llama.cpp
if [ ! -f "$LLAMA_CPP_DIR/build/bin/llama-server" ]; then
    echo -e "${YELLOW}llama.cpp server not found at $LLAMA_CPP_DIR${NC}"
    echo ""
    echo "To install llama.cpp with ROCm support (AMD):"
    echo ""
    echo "  git clone https://github.com/ggml-org/llama.cpp"
    echo "  cd llama.cpp"
    echo "  cmake -B build -DGGML_HIP=ON -DAMDGPU_TARGETS=\"gfx1151\""
    echo "  cmake --build build --config Release -j16"
    echo ""
    echo "Then set LLAMA_CPP_DIR to point to the directory."
    exit 1
fi

# Find model file
MODEL_PATH="$1"
if [ -z "$MODEL_PATH" ]; then
    # Look for .gguf files in model directory
    echo -e "${YELLOW}Looking for models in $MODEL_DIR...${NC}"
    
    # Find first .gguf file
    MODEL_PATH=$(find "$MODEL_DIR" -name "*.gguf" -type f 2>/dev/null | head -1)
    
    if [ -z "$MODEL_PATH" ]; then
        echo -e "${RED}No model found!${NC}"
        echo ""
        echo "Download a recommended model:"
        echo ""
        echo "  # For Ryzen AI MAX+ 395 (128GB) - Best quality:"
        echo "  pip install huggingface_hub hf_transfer"
        echo "  huggingface-cli download unsloth/Qwen3-235B-A22B-Instruct-2507-GGUF \\"
        echo "    --include 'UD-Q2_K_XL/*' --local-dir ./models"
        echo ""
        echo "  # For systems with less memory (~45GB):"
        echo "  huggingface-cli download TheBloke/Qwen2.5-72B-Instruct-GGUF \\"
        echo "    --include 'qwen2.5-72b-instruct.Q4_K_M.gguf' --local-dir ./models"
        echo ""
        exit 1
    fi
fi

echo -e "${GREEN}Found model: $MODEL_PATH${NC}"

# Get model size
MODEL_SIZE=$(du -h "$MODEL_PATH" | cut -f1)
echo -e "Model size: ${BLUE}$MODEL_SIZE${NC}"
echo ""

# Check available memory
if command -v free &> /dev/null; then
    TOTAL_MEM=$(free -g | awk '/^Mem:/{print $2}')
    AVAIL_MEM=$(free -g | awk '/^Mem:/{print $7}')
    echo -e "System memory: ${BLUE}${AVAIL_MEM}GB available${NC} / ${TOTAL_MEM}GB total"
fi

echo ""
echo -e "${YELLOW}Starting server with:${NC}"
echo "  Host: $HOST"
echo "  Port: $PORT"
echo "  Context: $CTX_SIZE tokens"
echo "  Threads: $THREADS"
echo "  GPU Layers: $GPU_LAYERS"
echo ""

# Build command
CMD="$LLAMA_CPP_DIR/build/bin/llama-server"
CMD="$CMD --model \"$MODEL_PATH\""
CMD="$CMD --host $HOST"
CMD="$CMD --port $PORT"
CMD="$CMD --ctx-size $CTX_SIZE"
CMD="$CMD --threads $THREADS"
CMD="$CMD --n-gpu-layers $GPU_LAYERS"
CMD="$CMD --flash-attn"

# Add MoE offloading for very large models
if [[ "$MODEL_PATH" == *"235B"* ]] || [[ "$MODEL_PATH" == *"MoE"* ]]; then
    echo -e "${YELLOW}Detected large MoE model - adding expert offloading${NC}"
    CMD="$CMD -ot '.ffn_.*_exps.=CPU'"
fi

echo ""
echo -e "${GREEN}Server starting...${NC}"
echo -e "API endpoint: ${BLUE}http://$HOST:$PORT/v1/chat/completions${NC}"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Run the server
eval $CMD
