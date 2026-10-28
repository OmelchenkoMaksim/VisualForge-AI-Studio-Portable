/* Load the production catalogs and runtime into a small DOM fixture. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
module.exports=function install(context){
  if(!vm.isContext(context))vm.createContext(context);
  for(const file of ['strings.js','i18n.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../App/frontend',file),'utf8'),context,{filename:file});
  return context;
};
