package plugins

import("os";"testing")

func TestDreamaVoicePackageUpgrade(t *testing.T){
 oldPath:=os.Getenv("DREAMA_TEST_OLD_PACKAGE");newPath:=os.Getenv("DREAMA_TEST_NEW_PACKAGE")
 if oldPath==""||newPath==""{t.Skip("local package fixtures not specified")}
 runtime,store,_:=newManagedRuntime(t);svc:=New(runtime,store)
 for _,path:=range []string{oldPath,newPath}{
  data,err:=os.ReadFile(path);if err!=nil{t.Fatal(err)}
  view,err:=svc.InstallUploaded("local-owner",data,"dreama-modalities.beeftv-plugin")
  if err!=nil{t.Fatalf("install %s: %v",path,err)}
  t.Log(view.Manifest.ID,view.Manifest.Version)
 }
}
