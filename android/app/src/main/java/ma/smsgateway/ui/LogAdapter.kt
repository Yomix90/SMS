package ma.smsgateway.ui

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import ma.smsgateway.R
import ma.smsgateway.data.LogEntry
import ma.smsgateway.databinding.ItemLogBinding
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class LogAdapter : ListAdapter<LogEntry, LogAdapter.Holder>(DIFF) {

    class Holder(val binding: ItemLogBinding) : RecyclerView.ViewHolder(binding.root)

    private val timeFormat = SimpleDateFormat("dd/MM HH:mm:ss", Locale.ROOT)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
        Holder(ItemLogBinding.inflate(LayoutInflater.from(parent.context), parent, false))

    override fun onBindViewHolder(holder: Holder, position: Int) {
        val entry = getItem(position)
        val ctx = holder.itemView.context
        holder.binding.time.text = timeFormat.format(Date(entry.time))
        holder.binding.text.text = entry.text
        val (icon, color) = when (entry.level) {
            "ok" -> R.drawable.ic_check to R.color.ok
            "warn" -> R.drawable.ic_warning to R.color.warn
            "error" -> R.drawable.ic_error to R.color.error
            "in" -> R.drawable.ic_arrow_down to R.color.incoming
            "out" -> R.drawable.ic_arrow_up to R.color.brand
            else -> R.drawable.ic_info to R.color.muted
        }
        holder.binding.icon.setImageResource(icon)
        holder.binding.icon.setColorFilter(ContextCompat.getColor(ctx, color))
    }

    private companion object {
        val DIFF = object : DiffUtil.ItemCallback<LogEntry>() {
            override fun areItemsTheSame(a: LogEntry, b: LogEntry) = a.id == b.id
            override fun areContentsTheSame(a: LogEntry, b: LogEntry) = a == b
        }
    }
}
