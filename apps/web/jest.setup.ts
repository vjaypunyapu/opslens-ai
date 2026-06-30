import "@testing-library/jest-dom";
import { TextDecoder, TextEncoder } from "util";

// jsdom doesn't expose TextEncoder/TextDecoder on the global object
Object.assign(global, { TextDecoder, TextEncoder });
