// Web Worker for Non-Blocking Layout Processing
// Consumes SSE streams and offloads JSON parsing from the main Three.js thread.

self.onmessage = async (e) => {
    const { type, payload, endpoint } = e.data;
    
    if (type === "PROCESS_LAYOUT_SSE") {
        try {
            const response = await fetch(endpoint, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let accumulatedJson = "";
            
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                
                const chunk = decoder.decode(value, { stream: true });
                accumulatedJson += chunk;
                
                // Parse lines formatted as Server-Sent Events (data: {...})
                const lines = chunk.split("\n");
                for (let line of lines) {
                    if (line.startsWith("data: ")) {
                        const dataStr = line.replace("data: ", "").trim();
                        if (dataStr === "[DONE]") break;
                        try {
                            const parsedData = JSON.parse(dataStr);
                            // Stream incremental updates back to main thread
                            self.postMessage({ type: "LAYOUT_STREAM_CHUNK", data: parsedData });
                        } catch (err) {
                            // incomplete chunk, ignore
                        }
                    }
                }
            }
            
            self.postMessage({ type: "LAYOUT_STREAM_COMPLETE", fullData: accumulatedJson });
            
        } catch (error) {
            self.postMessage({ type: "LAYOUT_STREAM_ERROR", error: error.message });
        }
    }
};
