// DragonShard-2: preserve the independent-JVM/i%N model; export evidence, not CLO predicates.
// @category DragonShard
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.framework.Application;
import ghidra.framework.data.OpenMode;
import ghidra.framework.store.db.PackedDatabase;
import ghidra.program.database.ProgramDB;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import com.google.gson.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.time.Instant;

public class DragonShardExport extends GhidraScript {
    private final Gson json = new Gson();
    private BufferedWriter writer;
    private long writtenBytes = 0, outputLimit = Long.MAX_VALUE;
    private Path outputDirectory;
    private void emit(JsonObject row) throws IOException {
        String text = json.toJson(row) + "\n";
        long length = text.getBytes(StandardCharsets.UTF_8).length;
        if (length > outputLimit - writtenBytes)
            throw new IOException("Shard output budget exhausted; partial evidence retained, no success marker");
        if (Files.getFileStore(outputDirectory).getUsableSpace() < length + 65536)
            throw new IOException("Insufficient space for next record; partial evidence retained");
        writer.write(text); writtenBytes += length;
    }
    private String rva(Address address) {
        try { return "0x" + Long.toHexString(address.subtract(currentProgram.getImageBase())); }
        catch (IllegalArgumentException e) { return ""; }
    }
    private JsonObject record(String kind) { JsonObject o=new JsonObject(); o.addProperty("kind",kind); return o; }
    private String bytes(Address address) {
        try {
            byte[] b=new byte[32]; int n=currentProgram.getMemory().getBytes(address,b);
            StringBuilder s=new StringBuilder(); for(int i=0;i<n;i++) s.append(String.format("%02x",b[i]&255));
            return s.toString();
        } catch(Exception e) { return ""; }
    }
    public void run() throws Exception {
        String[] a=getScriptArgs();
        String packedPath=System.getProperty("dragonshard.packed.path");
        Program original=currentProgram, packedProgram=null;
        if(packedPath!=null) {
            PackedDatabase packed=PackedDatabase.getPackedDatabase(new File(packedPath),false,monitor);
            db.DBHandle handle=null;
            try {
                handle=packed.open(monitor);
                packedProgram=new ProgramDB(handle,OpenMode.UPDATE,monitor,this);
                currentProgram=packedProgram;
            } catch(Exception e) {
                if(handle!=null) handle.close(); else packed.dispose();
                throw e;
            }
        }
        try { export(a); }
        finally {
            currentProgram=original;
            if(packedProgram!=null) packedProgram.release(this);
        }
    }
    private void export(String[] a) throws Exception {
        if(a.length<4 || a.length>7) throw new IllegalArgumentException("output.jsonl shard shards timeout [expected_sha256 [snapshot_id [max_output_bytes]]]");
        Path output=Paths.get(a[0]); int shard=Integer.parseInt(a[1]), shards=Integer.parseInt(a[2]), timeout=Integer.parseInt(a[3]);
        boolean segment=Boolean.getBoolean("dragonshard.export.segment");
        boolean symbolsOnly=Boolean.getBoolean("dragonshard.export.symbolsOnly");
        int ordinalStart=Integer.getInteger("dragonshard.ordinal.start",0);
        int ordinalEnd=Integer.getInteger("dragonshard.ordinal.end",Integer.MAX_VALUE);
        if(ordinalStart<0 || ordinalEnd<=ordinalStart) throw new IllegalArgumentException("Invalid ordinal interval");
        if(shards<1 || shard<0 || shard>=shards || timeout<1) throw new IllegalArgumentException("invalid shard or timeout");
        String sha=currentProgram.getExecutableSHA256();
        if(sha==null || sha.isEmpty()) throw new IllegalStateException("Program has no executable SHA256; establish binary identity first");
        if(a.length>=5 && !a[4].equalsIgnoreCase(sha)) throw new IllegalStateException("Binary SHA256 does not match manifest");
        if(Files.exists(output)) throw new IOException("Refusing to overwrite a completed export: "+output);
        Files.createDirectories(output.toAbsolutePath().getParent());
        outputDirectory=output.toAbsolutePath().getParent();
        if(a.length>=7) { outputLimit=Long.parseLong(a[6]); if(outputLimit<1) throw new IllegalArgumentException("Output budget must be positive"); }
        Path partial=Paths.get(output.toString()+".partial");
        DecompInterface dec=new DecompInterface();
        DecompileOptions options=new DecompileOptions(); options.grabFromProgram(currentProgram); dec.setOptions(options);
        if(!dec.openProgram(currentProgram)) throw new IOException(dec.getLastMessage());
        long start=System.nanoTime(); int total=0, attempted=0, ok=0, failed=0, timedout=0;
        try(BufferedWriter w=Files.newBufferedWriter(partial,StandardCharsets.UTF_8)) {
            writer=w; JsonObject header=record("program");
            header.addProperty("schema",2); header.addProperty("sha256",sha);
            header.addProperty("snapshot_id",a.length>=6?a[5]:"unspecified");
            header.addProperty("name",currentProgram.getName()); header.addProperty("project_path",System.getProperty("dragonshard.project.path",currentProgram.getDomainFile().getPathname()));
            header.addProperty("analysis_stage",System.getProperty("dragonshard.analysis.stage","manifest_audited_source"));
            header.addProperty("image_base",currentProgram.getImageBase().toString());
            header.addProperty("executable",currentProgram.getExecutablePath()); header.addProperty("language",currentProgram.getLanguageID().toString());
            header.addProperty("ghidra",Application.getApplicationVersion()); header.addProperty("created",Instant.now().toString());
            header.addProperty("shard",shard); header.addProperty("shards",shards); header.addProperty("timeout_seconds",timeout);
            if(segment) {
                header.addProperty("segment",true); header.addProperty("ordinal_start",ordinalStart);
                header.addProperty("ordinal_end",ordinalEnd);
            }
            if(symbolsOnly) header.addProperty("symbols_only",true);
            header.addProperty("reported_function_count",currentProgram.getFunctionManager().getFunctionCount()); emit(header);
            if(shard==0 && (!segment || symbolsOnly)) {
                SymbolIterator symbols=currentProgram.getSymbolTable().getAllSymbols(true);
                while(symbols.hasNext()) {
                    monitor.checkCancelled(); Symbol s=symbols.next(); JsonObject row=record("symbol");
                    row.addProperty("address",s.getAddress().toString()); row.addProperty("name",s.getName(true));
                    row.addProperty("symbol_type",s.getSymbolType().toString()); row.addProperty("source",s.getSource().toString());
                    row.addProperty("external",s.isExternal()); row.addProperty("export",currentProgram.getSymbolTable().isExternalEntryPoint(s.getAddress()));
                    emit(row);
                }
            }
            for(Function f:currentProgram.getFunctionManager().getFunctions(true)) {
                monitor.checkCancelled(); int ordinal=total++;
                if(symbolsOnly || ordinal%shards!=shard || ordinal<ordinalStart || ordinal>=ordinalEnd) continue;
                attempted++; JsonObject row=record("function");
                row.addProperty("ordinal",ordinal); row.addProperty("address",f.getEntryPoint().toString()); row.addProperty("rva",rva(f.getEntryPoint()));
                row.addProperty("name",f.getName(true)); row.addProperty("signature",f.getPrototypeString(false,false));
                row.addProperty("thunk",f.isThunk()); row.addProperty("body_bytes",f.getBody().getNumAddresses());
                JsonArray refs=new JsonArray(); StringBuilder refText=new StringBuilder();
                int instructionCount=0;
                for(Instruction ins:currentProgram.getListing().getInstructions(f.getBody(),true)) {
                    monitor.checkCancelled();
                    instructionCount++;
                    if(ins.getFlowType().isCall() && ins.getFlows().length==0) {
                        JsonObject ref=new JsonObject(); ref.addProperty("from",ins.getAddress().toString());
                        ref.addProperty("type","unresolved_indirect_call"); ref.addProperty("instruction",ins.toString()); refs.add(ref);
                    }
                    for(Reference r:ins.getReferencesFrom()) {
                        JsonObject ref=new JsonObject(); Address to=r.getToAddress();
                        ref.addProperty("from",r.getFromAddress().toString()); ref.addProperty("to",to.toString()); ref.addProperty("type",r.getReferenceType().toString());
                        ref.addProperty("call",r.getReferenceType().isCall());
                        Symbol symbol=currentProgram.getSymbolTable().getPrimarySymbol(to);
                        if(symbol!=null) { ref.addProperty("symbol",symbol.getName(true)); refText.append(symbol.getName(true)).append(' '); }
                        Function callee=currentProgram.getFunctionManager().getFunctionAt(to);
                        if(callee!=null) { ref.addProperty("callee",callee.getEntryPoint().toString()); ref.addProperty("callee_name",callee.getName(true)); }
                        if(r.isMemoryReference() && !r.getReferenceType().isFlow()) {
                            Data data=currentProgram.getListing().getDefinedDataAt(to);
                            if(data!=null) {
                                String value=data.getDefaultValueRepresentation(); ref.addProperty("data_type",data.getDataType().getName());
                                ref.addProperty("value",value); refText.append(value).append(' ');
                            }
                            // Raw data evidence permits GUID/vtable reconstruction; do not guess interface identities.
                            ref.addProperty("target_bytes",bytes(to));
                        }
                        refs.add(ref);
                    }
                }
                row.add("references",refs); row.addProperty("reference_text",refText.toString());
                row.addProperty("instruction_count",instructionCount);
                Function thunkTarget=f.isThunk()?f.getThunkedFunction(true):null;
                row.addProperty("synthetic_external_thunk",instructionCount==0 && thunkTarget!=null && thunkTarget.isExternal());
                long fnStart=System.nanoTime();
                try {
                    DecompileResults result=dec.decompileFunction(f,timeout,monitor);
                    if(result.decompileCompleted() && result.getDecompiledFunction()!=null) {
                        row.addProperty("status","ok"); row.addProperty("c",result.getDecompiledFunction().getC()); ok++;
                    } else {
                        boolean expired=result.isTimedOut(); row.addProperty("status",expired?"timeout":"failed");
                        row.addProperty("error",result.getErrorMessage()); if(expired)timedout++;else failed++;
                    }
                } catch(Exception e) {
                    monitor.checkCancelled(); row.addProperty("status","failed"); row.addProperty("error",e.toString()); failed++;
                }
                row.addProperty("seconds",(System.nanoTime()-fnStart)/1e9); emit(row);
                if(attempted%32==0) w.flush();
            }
            monitor.checkCancelled(); JsonObject end=record("complete");
            end.addProperty("total_enumerated",total); end.addProperty("attempted",attempted); end.addProperty("ok",ok);
            end.addProperty("failed",failed); end.addProperty("timeout",timedout); end.addProperty("seconds",(System.nanoTime()-start)/1e9);
            emit(end);
        } finally { dec.dispose(); }
        Files.move(partial,output,StandardCopyOption.ATOMIC_MOVE);
        println("DragonShard-2 shard "+shard+"/"+shards+" complete: "+ok+" decompiled, "+failed+" failed, "+timedout+" timed out");
    }
}
