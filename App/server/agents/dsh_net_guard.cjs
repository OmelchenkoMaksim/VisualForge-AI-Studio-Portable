'use strict';
// Preloaded into DeepSeek Harness with `node --require`: the agent may connect only to the studio's model gateway
// (VF_ALLOWED_ENDPOINT, "host:port") and to sockets it listens on itself; anything it listens on is bound to loopback.
// dsh ships DeepSeek telemetry, web search and cloud model routes; the studio disables them in its config patch, and this
// guard makes "offline" hold even for a plugin that ignores that config. Child processes (the agent's shell commands) are
// not affected, as with the other agents.
const net = require('node:net');
const dns = require('node:dns');
const dgram = require('node:dgram');
// No agent transport uses UDP; TCP hooks do not cover datagrams or UDP DNS.
dgram.Socket.prototype.send = function () { throw refused('UDP'); };
dgram.Socket.prototype.connect = function () { throw refused('UDP'); };

const allowed = String(process.env.VF_ALLOWED_ENDPOINT || '').toLowerCase();
const allowedHost = allowed.slice(0, allowed.lastIndexOf(':'));
const own = new Set();
const LOOPBACK = new Set(['127.0.0.1', 'localhost', '::1']);

function refused(target) {
  process.stderr.write('VisualForge: refused a network connection to ' + target + '\n');
  const error = new Error('VisualForge: network access is disabled for the agent (' + target + ')');
  error.code = 'ECONNREFUSED';
  return error;
}

function target(args) {
  let options = args[0];
  if (Array.isArray(options)) options = options[0];  // the internal normalized form
  if (options && typeof options === 'object') {
    if (options.path) return null;  // named pipe: local IPC
    return { host: String(options.host || 'localhost').toLowerCase(), port: Number(options.port) };
  }
  if (typeof options === 'string' && !/^\d+$/.test(options)) return null;
  return { host: String(typeof args[1] === 'string' ? args[1] : 'localhost').toLowerCase(), port: Number(options) };
}

const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...args) {
  const to = target(args);
  if (to) {
    const key = to.host + ':' + to.port;
    if (key !== allowed && !(LOOPBACK.has(to.host) && own.has(to.port))) {
      const error = refused(key);
      process.nextTick(() => this.destroy(error));
      return this;
    }
  }
  return connect.apply(this, args);
};

const listen = net.Server.prototype.listen;
net.Server.prototype.listen = function (...args) {
  const first = args[0];
  if (first && typeof first === 'object' && !first.path && first.port !== undefined) {
    args[0] = Object.assign({}, first, { host: '127.0.0.1' });
  } else if (typeof first === 'number' || (typeof first === 'string' && /^\d+$/.test(first))) {
    args.splice(1, typeof args[1] === 'string' ? 1 : 0, '127.0.0.1');
  }
  this.once('listening', () => {
    const address = this.address();
    if (address && typeof address === 'object') own.add(address.port);
  });
  return listen.apply(this, args);
};

const permittedName = (name) => name === allowedHost || LOOPBACK.has(name);
const lookup = dns.lookup;
dns.lookup = function (hostname, options, callback) {
  if (typeof options === 'function') { callback = options; options = {}; }
  const name = String(hostname).toLowerCase();
  if (!permittedName(name)) {
    process.nextTick(() => callback(refused(name)));
    return {};
  }
  return lookup.call(this, hostname, options, callback);
};
// c-ares resolvers bypass both net.Socket and dgram.Socket hooks.
for (const owner of [dns, dns.Resolver.prototype, dns.promises, dns.promises.Resolver.prototype]) {
  for (const method of Object.getOwnPropertyNames(owner)) {
    if (!method.startsWith('resolve') && method !== 'reverse') continue;
    if (typeof owner[method] !== 'function') continue;
    owner[method] = function () { throw refused('DNS ' + method); };
  }
}
const promisesLookup = dns.promises.lookup;
dns.promises.lookup = function (hostname, options) {
  const name = String(hostname).toLowerCase();
  return permittedName(name) ? promisesLookup.call(this, hostname, options) : Promise.reject(refused(name));
};
