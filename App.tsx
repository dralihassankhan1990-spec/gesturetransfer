import React, { useState, useRef, useEffect } from 'react';
import { useHandTracking } from './hooks/useHandTracking';
import { ParticleView } from './components/ParticleView';
import { CarouselView } from './components/CarouselView';
import { AppMode, GestureType } from './types';
import { Camera, Layers, Image as ImageIcon, Hand, Menu, X } from 'lucide-react';

const App: React.FC = () => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [activeMode, setActiveMode] = useState<AppMode>(AppMode.PARTICLES);
  const [cameraEnabled, setCameraEnabled] = useState(false);
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);
  const [sidebarOpen, setSidebarOpen] = useState(!isMobile);

  // Conditionally run hook logic or just prepare refs
  const { handState, isLoading } = useHandTracking(videoRef);

  const stopCamera = () => {
    if (videoRef.current && videoRef.current.srcObject) {
      const stream = videoRef.current.srcObject as MediaStream;
      const tracks = stream.getTracks();
      tracks.forEach(track => track.stop());
      videoRef.current.srcObject = null;
      setCameraEnabled(false);
    }
  };

  const startCamera = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: 640,
          height: 480,
          facingMode: "user"
        }
      });
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        videoRef.current.addEventListener('loadeddata', () => {
          setCameraEnabled(true);
        });
      }
    } catch (err) {
      console.error("Camera error:", err);
      alert("Please allow camera access to use gesture controls.");
    }
  };

  const toggleCamera = () => {
    if (cameraEnabled) {
      stopCamera();
    } else {
      startCamera();
    }
  };

  const ws = useRef<WebSocket | null>(null);

  useEffect(() => {
    // Connect to local WebSocket server
    ws.current = new WebSocket('ws://localhost:3001');

    return () => {
      ws.current?.close();
    };
  }, []);

  // Send gesture/pointer messages to bridge with throttling
  useEffect(() => {
    if (!ws.current) return;

    // Simple gesture messages
    // Debounce / hold logic: require some gestures to persist briefly to avoid false positives
    const holdRequired = {
      [GestureType.POINTING]: 600, // ms to hold before 'laser'
      [GestureType.CLOSED_FIST]: 800, // ms to hold to 'close'
      [GestureType.OPEN_PALM]: 1000, // ms to hold to 'start'
      [GestureType.PINCH]: 200,
      [GestureType.SPREAD]: 200
    } as Record<string, number>;

    const g = handState.gesture;
    if (g === GestureType.SWIPE_LEFT || g === GestureType.SWIPE_RIGHT) {
      // swipes are detected by velocity in the hook and are momentary — send immediately
      ws.current.send(JSON.stringify({ type: 'gesture', gesture: g }));
    } else if (g === GestureType.PINCH || g === GestureType.SPREAD || g === GestureType.CLOSED_FIST || g === GestureType.POINTING || g === GestureType.OPEN_PALM) {
      // require hold
      const now = performance.now();
      (ws as any)._gestureState = (ws as any)._gestureState || { last: GestureType.NONE, startTs: 0, sent: false };
      const st = (ws as any)._gestureState;
      if (st.last !== g) { st.last = g; st.startTs = now; st.sent = false; }
      const required = holdRequired[g] || 150;
      if (!st.sent && (now - st.startTs) >= required) {
        // send once
        ws.current.send(JSON.stringify({ type: 'gesture', gesture: g }));
        st.sent = true;
      }
    } else {
      // reset gesture hold when none
      if ((ws as any)._gestureState) {
        (ws as any)._gestureState.last = GestureType.NONE;
        (ws as any)._gestureState.sent = false;
      }
    }

    // Pointer movement when pointing: throttle to ~30fps
    const lastPointerRef: { current: number | null } = (ws as any).lastPointerRef || ((ws as any).lastPointerRef = { current: null });
    if (handState.detected && handState.gesture === GestureType.POINTING) {
      const now = performance.now();
      if (!lastPointerRef.current || now - lastPointerRef.current > 33) {
        lastPointerRef.current = now;
        ws.current.send(JSON.stringify({ type: 'pointer', x: handState.position.x, y: handState.position.y }));
      }
    }

  }, [handState]);
  //   // Add to App.tsx
  // const wsBroadcastGesture = (gesture: string) => {
  //   if (window.wsServer) {
  //     window.wsServer.send(JSON.stringify({
  //       type: gesture,
  //       timestamp: Date.now()
  //     }));
  //   }
  // };

  // // In your gesture detection logic, broadcast gestures
  // useEffect(() => {
  //   if (handState.detected && handState.gesture !== GestureType.NONE) {
  //     wsBroadcastGesture(handState.gesture);
  //   }
  // }, [handState.gesture]);

  // Add to your existing gesture detection

  // Handle window resize
  useEffect(() => {
    const handleResize = () => {
      const mobile = window.innerWidth < 768;
      setIsMobile(mobile);
      setSidebarOpen(!mobile);
    };

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  return (
    <div className="w-screen h-screen bg-slate-900 text-white overflow-hidden flex flex-col md:flex-row">
      {/* Mobile Header */}
      <div className="md:hidden flex items-center justify-between p-4 bg-slate-950 border-b border-white/10 z-50">
        <div className="flex items-center">
          <button
            onClick={() => setSidebarOpen(!sidebarOpen)}
            className="p-2 mr-4 text-gray-400 hover:text-white"
          >
            {sidebarOpen ? <X size={24} /> : <Menu size={24} />}
          </button>
          <div className="text-blue-500">
            <Hand size={28} />
          </div>
        </div>
        <div className="text-sm font-medium">
          {activeMode === AppMode.PARTICLES ? 'Particle Physics' : 'Image Carousel'}
        </div>
        <div className="w-8"></div> {/* Spacer for flex alignment */}
      </div>

      {/* Sidebar / Navigation */}
      <nav
        className={`${sidebarOpen ? 'flex' : 'hidden'} md:flex w-64 md:w-20 bg-slate-950 border-r border-white/10 flex-col items-center py-8 gap-8 z-40 fixed md:static h-screen md:h-auto`}
        style={isMobile ? { width: '16rem' } : {}}
      >
        {isMobile && (
          <div className="text-blue-500 mb-8 flex items-center">
            <Hand size={32} className="mr-3" />
            <span className="text-xl font-semibold">GestureFlow</span>
          </div>
        )}
        {!isMobile && (
          <div className="text-blue-500 mb-4">
            <Hand size={32} />
          </div>
        )}

        <button
          onClick={() => {
            setActiveMode(AppMode.PARTICLES);
            if (isMobile) setSidebarOpen(false);
          }}
          className={`p-3 rounded-xl transition-all flex items-center w-full md:w-auto justify-center md:justify-start ${activeMode === AppMode.PARTICLES ? 'bg-blue-600 shadow-lg shadow-blue-500/30' : 'hover:bg-white/5 text-gray-400'}`}
          title="Particle Physics"
        >
          <Layers size={24} />
          {isMobile && <span className="ml-3">Particle Physics</span>}
        </button>

        <button
          onClick={() => {
            setActiveMode(AppMode.CAROUSEL);
            if (isMobile) setSidebarOpen(false);
          }}
          className={`p-3 rounded-xl transition-all flex items-center w-full md:w-auto justify-center md:justify-start ${activeMode === AppMode.CAROUSEL ? 'bg-blue-600 shadow-lg shadow-blue-500/30' : 'hover:bg-white/5 text-gray-400'}`}
          title="Image Carousel"
        >
          <ImageIcon size={24} />
          {isMobile && <span className="ml-3">Image Carousel</span>}
        </button>

        <div className="mt-auto flex flex-col items-center gap-2 w-full px-4">
          <div className="text-xs text-gray-400 mb-1 text-center">
            {isMobile ? 'Camera Preview' : 'Camera'}
          </div>
          <div className="relative w-full max-w-xs h-20 bg-black rounded overflow-hidden border border-gray-700">
            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              className="w-full h-full object-cover opacity-70"
              style={{ transform: 'scaleX(-1)' }}
              onClick={toggleCamera}
            />
            <button
              onClick={(e) => {
                e.stopPropagation();
                toggleCamera();
              }}
              className={`absolute inset-0 flex items-center justify-center transition-colors ${cameraEnabled
                  ? 'bg-red-500/20 hover:bg-red-500/30'
                  : 'bg-gray-900/80 hover:bg-gray-800/90'
                }`}
            >
              {cameraEnabled ? (
                <>
                  <div className="w-4 h-4 bg-red-500 rounded-full mr-2"></div>
                  {isMobile && <span className="text-xs">Stop Camera</span>}
                </>
              ) : (
                <>
                  <Camera size={20} className="mr-2" />
                  {isMobile && <span className="text-xs">Enable Camera</span>}
                </>
              )}
            </button>
            {handState.detected && (
              <div className="absolute top-1 right-1 w-2 h-2 bg-green-500 rounded-full animate-pulse" />
            )}
          </div>
        </div>
      </nav>

      {/* Main Content Area */}
      <main className="flex-1 relative h-full overflow-auto">
        {/* Overlay to close sidebar on mobile when clicking outside */}
        {isMobile && sidebarOpen && (
          <div
            className="fixed inset-0 bg-black/50 z-30 md:hidden"
            onClick={() => setSidebarOpen(false)}
          />
        )}
        {isLoading && cameraEnabled && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/50 z-50 backdrop-blur-sm">
            <div className="text-blue-400 font-mono animate-pulse">Loading AI Vision Model...</div>
          </div>
        )}

        {activeMode === AppMode.PARTICLES && (
          <ParticleView handState={handState} />
        )}

        {activeMode === AppMode.CAROUSEL && (
          <CarouselView handState={handState} />
        )}

        {/* Gesture Feedback Overlay */}
        {handState.detected && (
          <div className="absolute top-4 right-4 bg-black/60 backdrop-blur px-4 py-2 rounded-lg border border-white/10 pointer-events-none">
            <div className="flex items-center gap-3">
              <span className="text-xs font-mono text-gray-400">GESTURE DETECTED</span>
              <span className="font-bold text-blue-400">
                {handState.gesture === GestureType.CLOSED_FIST && "✊ CLOSED FIST"}
                {handState.gesture === GestureType.OPEN_PALM && "✋ OPEN PALM"}
                {handState.gesture === GestureType.POINTING && "☝️ POINTING"}
                {handState.gesture === GestureType.NONE && "Detecting..."}
              </span>
            </div>
          </div>
        )}
      </main>
    </div>
  );
};

export default App;
