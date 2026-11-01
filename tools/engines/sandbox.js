const vm=require('vm'),fs=require('fs');
function ctx(){ const c={console, setTimeout, clearTimeout, setInterval, clearInterval, queueMicrotask, TextEncoder, TextDecoder, URL, Promise}; c.globalThis=c; c.window=c; c.self=c; return vm.createContext(c); }
const tests={
 ts:["LF_TS('const f=(n:number):number=>n*2; interface X{a:string}; console.log(\"ts\", f(21))')", true],
 lua:["(()=>{let o='';LF_LUA('local t={} for i=1,5 do t[#t+1]=i*i end print(\"lua\", table.concat(t,\",\")) io.write(\"x\",1,\"\\\\n\")', s=>o+=s);return o})()"],
 ruby:["(()=>{let o='';LF_RUBY('class A; def hi(n); (1..n).map { |i| i * i }; end; end\\nputs \"ruby #{A.new.hi(4).inspect}\"\\np({a: 1})', s=>o+=s);return o})()"],
 cpp:["(()=>{let o='';LF_CPP('#include <iostream>\\nusing namespace std;\\nint main(){ for(int i=1;i<=3;i++) cout << i*i << \" \"; cout << endl; return 0; }', s=>o+=s);return o})()"],
 php:["(async()=>{let o='';await LF_PHP('echo \"php \" . implode(\",\", array_map(function($x){return $x*$x;}, [1,2,3])) . \"\\\\n\";', s=>o+=s);return o})()"],
};
(async()=>{for(const [e,[expr,evalTs]] of Object.entries(tests)){
  const c=ctx();
  try{ vm.runInContext(fs.readFileSync('out/'+e+'.js','utf8'),c);
    let r=vm.runInContext(expr,c); r=await r;
    if(evalTs){ vm.runInContext(r,c); console.log(e,'OK (transpiled)'); } else console.log(e,'OK',JSON.stringify(r));
  }catch(err){ console.log(e,'FAIL',String(err&&err.message||err).slice(0,200)); }
}})();
