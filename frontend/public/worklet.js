class PCMProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this.buffer = new Float32Array();
    }

    process(inputs) {
        const input = inputs[0];
        if (!input || !input.length) return true;

        const channelData = input[0]; // Mono processing

        // We need to convert 32-bit float (browser default) to 16-bit PCM for the backend VAD
        // We also need to downsample if the context is 44.1/48kHz, but usually we handle that 
        // by setting context sampleRate. For now, we assume input is getting resampled 
        // or we just send raw chunks and handle complexity.

        // The backend expects mono PCM/WAV-style audio:
        // We stream 16kHz little-endian PCM to the backend VAD segmenter.
        // The AudioContext in the hook will handle the sample rate (16000).
        // Here we just convert Float32 -> Int16.

        const int16Data = this.float32ToInt16(channelData);

        // Send to main thread
        this.port.postMessage(int16Data.buffer, [int16Data.buffer]);

        return true;
    }

    float32ToInt16(float32Array) {
        const int16Array = new Int16Array(float32Array.length);
        for (let i = 0; i < float32Array.length; i++) {
            let s = Math.max(-1, Math.min(1, float32Array[i]));
            int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return int16Array;
    }
}

registerProcessor("pcm-processor", PCMProcessor);
