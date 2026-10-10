export enum GestureType {
  NONE = 'NONE',
  OPEN_PALM = 'OPEN_PALM',
  CLOSED_FIST = 'CLOSED_FIST',
  POINTING = 'POINTING',
  SWIPE_LEFT = 'SWIPE_LEFT',
  SWIPE_RIGHT = 'SWIPE_RIGHT',
  PINCH = 'PINCH',
  SPREAD = 'SPREAD'
}

export interface HandPosition {
  x: number; // Normalized 0-1
  y: number; // Normalized 0-1
  z: number; // Normalized depth
}

export interface HandState {
  detected: boolean;
  position: HandPosition;
  gesture: GestureType;
  tilt: number; // radian rotation of hand
}

export enum ParticleShape {
  CIRCLE = 'CIRCLE',
  SQUARE = 'SQUARE',
  SOLAR = 'SOLAR',
  GALAXY = 'GALAXY'
}

export enum AppMode {
  PARTICLES = 'PARTICLES',
  CAROUSEL = 'CAROUSEL'
}